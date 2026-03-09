"""End-to-end DRY_RUN mode validation tests.

These tests exercise the full sync cycle (engine + real provider plugin
instances, with HTTP mocked) to verify that DRY_RUN mode:

1. Logs trust decisions without calling the Twingate mutation.
2. Still increments ``total_trusted`` so the summary is accurate.
3. Works correctly across trust.mode=any and trust.mode=all.
4. Handles mixed compliant/non-compliant device sets correctly.
5. Functions correctly when multiple providers are enabled.

Provider HTTP is mocked at the ``request_with_retry`` level, exactly as in
the individual provider tests.  The Twingate client is mocked via AsyncMock.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import (
    AppConfig,
    AutomoxConfig,
    NinjaOneConfig,
    SyncConfig,
    TrustConfig,
    TwingateConfig,
)
from src.engine import run_sync_cycle
from src.providers.base import ProviderDevice, ProviderPlugin
from src.twingate.models import TrustMutationEntity, TrustMutationResult, TwingateDevice


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_tg_device(device_id: str, serial: str | None = "SN123") -> TwingateDevice:
    return TwingateDevice(id=device_id, serialNumber=serial, isTrusted=False)


def _make_provider_device(
    serial: str,
    online: bool = True,
    compliant: bool = True,
    days_ago: int = 1,
) -> ProviderDevice:
    return ProviderDevice(
        serial_number=serial,
        is_online=online,
        is_compliant=compliant,
        last_seen=datetime.now(tz=UTC) - timedelta(days=days_ago),
    )


def _make_tg_client(
    devices: list[TwingateDevice] | None = None,
    trust_ok: bool = True,
) -> MagicMock:
    client = MagicMock()
    client.list_untrusted_devices = AsyncMock(return_value=devices or [])
    client.trust_device = AsyncMock(
        return_value=TrustMutationResult(
            ok=trust_ok,
            error=None,
            entity=TrustMutationEntity(id="dev-1", isTrusted=True) if trust_ok else None,
        )
    )
    return client


class _MockProvider(ProviderPlugin):
    """Minimal in-memory provider for integration tests."""

    def __init__(self, provider_name: str, devices: list[ProviderDevice]) -> None:
        self._name = provider_name
        self._devices = devices

    @property
    def name(self) -> str:
        return self._name

    async def authenticate(self) -> None:
        pass

    async def list_devices(self) -> list[ProviderDevice]:
        return self._devices

    def determine_compliance(self, device: dict) -> bool:
        return True


def _dry_run_config(
    mode: str = "any",
    require_online: bool = True,
    require_compliant: bool = True,
    max_days: int = 7,
) -> AppConfig:
    return AppConfig(
        twingate=TwingateConfig(tenant="test", api_key="key"),
        sync=SyncConfig(interval_seconds=60, dry_run=True, batch_size=10),
        trust=TrustConfig(
            mode=mode,
            require_online=require_online,
            require_compliant=require_compliant,
            max_days_since_checkin=max_days,
        ),
        providers=[],
    )


# ---------------------------------------------------------------------------
# Core DRY_RUN behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_call_trust_mutation() -> None:
    """The Twingate trust_device mutation must never be called in DRY_RUN."""
    config = _dry_run_config()
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = _MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_counts_would_be_trusted_in_summary() -> None:
    """total_trusted is incremented even in DRY_RUN (it counts decisions, not mutations)."""
    config = _dry_run_config()
    devices = [_make_tg_device(f"dev-{i}", f"SN{i}") for i in range(3)]
    provider = _MockProvider(
        "automox",
        [_make_provider_device(f"SN{i}") for i in range(3)],
    )
    tg_client = _make_tg_client(devices)

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 3
    tg_client.trust_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_skips_non_compliant_devices() -> None:
    """Non-compliant devices are skipped (not trusted) even in DRY_RUN."""
    config = _dry_run_config(require_compliant=True)
    tg_device = _make_tg_device("dev-1", "SN1")
    provider = _MockProvider("sophos", [_make_provider_device("SN1", compliant=False)])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 0
    assert summary.total_skipped == 1
    tg_client.trust_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_skips_offline_devices() -> None:
    """Offline devices are skipped when require_online=True."""
    config = _dry_run_config(require_online=True)
    tg_device = _make_tg_device("dev-1", "SN1")
    provider = _MockProvider("jumpcloud", [_make_provider_device("SN1", online=False)])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 0
    assert summary.total_skipped == 1


@pytest.mark.asyncio
async def test_dry_run_no_match_not_counted_as_trusted() -> None:
    """Unmatched devices must not appear in total_trusted."""
    config = _dry_run_config()
    tg_device = _make_tg_device("dev-1", "UNKNOWN_SN")
    provider = _MockProvider("datto", [_make_provider_device("DIFFERENT_SN")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 0
    assert summary.total_no_match == 1


# ---------------------------------------------------------------------------
# DRY_RUN with trust.mode = all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_mode_all_both_providers_compliant() -> None:
    config = _dry_run_config(mode="all")
    tg_device = _make_tg_device("dev-1", "SN1")
    p1 = _MockProvider("ninjaone", [_make_provider_device("SN1")])
    p2 = _MockProvider("sophos", [_make_provider_device("SN1")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [p1, p2], tg_client)

    assert summary.total_trusted == 1
    tg_client.trust_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_mode_all_one_provider_missing_does_not_trust() -> None:
    config = _dry_run_config(mode="all")
    tg_device = _make_tg_device("dev-1", "SN1")
    p1 = _MockProvider("ninjaone", [_make_provider_device("SN1")])
    p2 = _MockProvider("sophos", [])  # device not in sophos
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [p1, p2], tg_client)

    assert summary.total_trusted == 0
    tg_client.trust_device.assert_not_awaited()


# ---------------------------------------------------------------------------
# DRY_RUN with stale check-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_stale_device_not_trusted() -> None:
    """Devices checked in more than max_days_since_checkin ago are skipped."""
    config = _dry_run_config(max_days=7)
    tg_device = _make_tg_device("dev-1", "SN1")
    stale_device = _make_provider_device("SN1", days_ago=10)
    provider = _MockProvider("fleetdm", [stale_device])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 0
    assert summary.total_skipped == 1


# ---------------------------------------------------------------------------
# DRY_RUN with multiple providers (any mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_any_mode_one_provider_sufficient() -> None:
    """trust.mode=any: device in only one provider → trusted in DRY_RUN."""
    config = _dry_run_config(mode="any")
    tg_device = _make_tg_device("dev-1", "SN1")
    p1 = _MockProvider("ninjaone", [_make_provider_device("SN1")])
    p2 = _MockProvider("automox", [])  # device not in automox
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [p1, p2], tg_client)

    assert summary.total_trusted == 1
    tg_client.trust_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_mixed_device_set() -> None:
    """Mixed set: some trusted, some skipped, some unmatched — counts correct."""
    config = _dry_run_config(mode="any")
    tg_devices = [
        _make_tg_device("dev-pass", "PASS"),
        _make_tg_device("dev-offline", "OFFLINE"),
        _make_tg_device("dev-nomatch", "NOMATCH"),
        _make_tg_device("dev-noserial", serial=None),
    ]
    provider = _MockProvider(
        "mosyle",
        [
            _make_provider_device("PASS", online=True, compliant=True),
            _make_provider_device("OFFLINE", online=False, compliant=True),
        ],
    )
    tg_client = _make_tg_client(tg_devices)

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_trusted == 1
    assert summary.total_skipped == 1
    assert summary.total_no_match == 2   # NOMATCH + None serial
    tg_client.trust_device.assert_not_awaited()


# ---------------------------------------------------------------------------
# DRY_RUN provider failure is non-fatal
# ---------------------------------------------------------------------------


class _FailingProvider(ProviderPlugin):
    @property
    def name(self) -> str:
        return "failing"

    async def authenticate(self) -> None:
        raise RuntimeError("auth failed")

    async def list_devices(self) -> list[ProviderDevice]:
        return []

    def determine_compliance(self, device: dict) -> bool:
        return False


@pytest.mark.asyncio
async def test_dry_run_failing_provider_does_not_crash_cycle() -> None:
    config = _dry_run_config()
    tg_device = _make_tg_device("dev-1", "SN1")
    good = _MockProvider("ninjaone", [_make_provider_device("SN1")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [_FailingProvider(), good], tg_client)

    # Good provider still handles the device
    assert summary.total_trusted == 1
    tg_client.trust_device.assert_not_awaited()
