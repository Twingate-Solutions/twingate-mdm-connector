"""Unit tests for src/engine.py — providers and Twingate client are mocked."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AppConfig, SyncConfig, TrustConfig, TwingateConfig
from src.engine import CycleSummary, run_sync_cycle
from src.providers.base import ProviderDevice, ProviderPlugin
from src.twingate.models import TrustMutationEntity, TrustMutationResult, TwingateDevice


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_config(
    mode: str = "any",
    dry_run: bool = False,
    require_online: bool = True,
    require_compliant: bool = True,
    max_days: int = 7,
) -> AppConfig:
    return AppConfig(
        twingate=TwingateConfig(tenant="test", api_key="key"),
        sync=SyncConfig(interval_seconds=60, dry_run=dry_run, batch_size=10),
        trust=TrustConfig(
            mode=mode,
            require_online=require_online,
            require_compliant=require_compliant,
            max_days_since_checkin=max_days,
        ),
        providers=[],
    )


def _make_tg_device(device_id: str, serial: str | None = "SN123") -> TwingateDevice:
    return TwingateDevice(id=device_id, serialNumber=serial, isTrusted=False)


def _make_provider_device(serial: str, online: bool = True, compliant: bool = True) -> ProviderDevice:
    return ProviderDevice(
        serial_number=serial,
        is_online=online,
        is_compliant=compliant,
        last_seen=datetime.now(tz=UTC) - timedelta(hours=1),
    )


class MockProvider(ProviderPlugin):
    """A simple mock provider that returns a fixed list of devices."""

    def __init__(self, name_: str, devices: list[ProviderDevice]) -> None:
        self._name = name_
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


class FailingProvider(ProviderPlugin):
    """A provider that always raises during fetch."""

    @property
    def name(self) -> str:
        return "failing"

    async def authenticate(self) -> None:
        raise RuntimeError("Auth failed")

    async def list_devices(self) -> list[ProviderDevice]:
        return []

    def determine_compliance(self, device: dict) -> bool:
        return False


def _make_tg_client(
    devices: list[TwingateDevice] | None = None,
    trust_ok: bool = True,
) -> MagicMock:
    client = MagicMock()
    client.list_untrusted_devices = AsyncMock(return_value=devices or [])
    client.trust_device = AsyncMock(
        return_value=TrustMutationResult(
            ok=trust_ok,
            error=None if trust_ok else "mutation failed",
            entity=TrustMutationEntity(id="dev-1", isTrusted=trust_ok) if trust_ok else None,
        )
    )
    return client


# ---------------------------------------------------------------------------
# Basic cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_trusts_matching_device() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_awaited_once_with("dev-1")
    assert summary.total_trusted == 1
    assert summary.total_no_match == 0
    assert summary.total_skipped == 0


@pytest.mark.asyncio
async def test_cycle_dry_run_does_not_call_trust_mutation() -> None:
    config = _make_config(dry_run=True)
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_not_awaited()
    assert summary.total_trusted == 1  # counted even in dry_run


@pytest.mark.asyncio
async def test_cycle_no_match_device_not_trusted() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", "UNKNOWN_SN")
    provider = MockProvider("ninjaone", [_make_provider_device("DIFFERENT_SN")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_not_awaited()
    assert summary.total_no_match == 1
    assert summary.total_trusted == 0


@pytest.mark.asyncio
async def test_cycle_device_with_no_serial_skipped() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", serial=None)
    provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_not_awaited()
    assert summary.total_no_match == 1


@pytest.mark.asyncio
async def test_cycle_skips_non_compliant_device() -> None:
    config = _make_config(require_compliant=True)
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = MockProvider("ninjaone", [_make_provider_device("SN123", compliant=False)])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [provider], tg_client)

    tg_client.trust_device.assert_not_awaited()
    assert summary.total_skipped == 1
    assert summary.total_trusted == 0


# ---------------------------------------------------------------------------
# Provider failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_provider_does_not_crash_cycle() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", "SN123")
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [FailingProvider()], tg_client)

    # No providers available — should return early without trusting
    tg_client.trust_device.assert_not_awaited()
    assert summary.provider_stats[0].available is False
    assert summary.provider_stats[0].errors == 1


@pytest.mark.asyncio
async def test_one_failing_provider_does_not_block_other() -> None:
    config = _make_config(mode="any")
    tg_device = _make_tg_device("dev-1", "SN123")
    good_provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [FailingProvider(), good_provider], tg_client)

    # Good provider still trusted the device
    tg_client.trust_device.assert_awaited_once_with("dev-1")
    assert summary.total_trusted == 1


# ---------------------------------------------------------------------------
# Trust mutation failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trust_mutation_failure_counted_as_error() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device], trust_ok=False)

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_errors == 1
    assert summary.total_trusted == 0


@pytest.mark.asyncio
async def test_trust_mutation_exception_does_not_crash_cycle() -> None:
    config = _make_config()
    tg_device = _make_tg_device("dev-1", "SN123")
    provider = MockProvider("ninjaone", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])
    tg_client.trust_device = AsyncMock(side_effect=RuntimeError("network down"))

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_errors == 1
    assert summary.total_trusted == 0


# ---------------------------------------------------------------------------
# trust.mode = all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trust_mode_all_both_pass() -> None:
    config = _make_config(mode="all")
    tg_device = _make_tg_device("dev-1", "SN123")
    p1 = MockProvider("ninjaone", [_make_provider_device("SN123")])
    p2 = MockProvider("sophos", [_make_provider_device("SN123")])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [p1, p2], tg_client)

    tg_client.trust_device.assert_awaited_once_with("dev-1")
    assert summary.total_trusted == 1


@pytest.mark.asyncio
async def test_trust_mode_all_one_missing_does_not_trust() -> None:
    config = _make_config(mode="all")
    tg_device = _make_tg_device("dev-1", "SN123")
    # Only p1 has the device; p2 has no devices
    p1 = MockProvider("ninjaone", [_make_provider_device("SN123")])
    p2 = MockProvider("sophos", [])
    tg_client = _make_tg_client([tg_device])

    summary = await run_sync_cycle(config, [p1, p2], tg_client)

    tg_client.trust_device.assert_not_awaited()
    # Device was found in p1 (matched) but not trusted (all mode)
    assert summary.total_trusted == 0


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_counts_are_accurate() -> None:
    config = _make_config(mode="any")
    tg_devices = [
        _make_tg_device("dev-trusted", "MATCH"),
        _make_tg_device("dev-skipped", "OFFLINE"),
        _make_tg_device("dev-nomatch", "UNKNOWN"),
        _make_tg_device("dev-noserial", serial=None),
    ]
    provider_devices = [
        _make_provider_device("MATCH", online=True, compliant=True),
        _make_provider_device("OFFLINE", online=False, compliant=False),
    ]
    provider = MockProvider("ninjaone", provider_devices)
    tg_client = _make_tg_client(tg_devices)

    summary = await run_sync_cycle(config, [provider], tg_client)

    assert summary.total_untrusted == 4
    assert summary.total_trusted == 1
    assert summary.total_skipped == 1
    assert summary.total_no_match == 2  # UNKNOWN + None serial
