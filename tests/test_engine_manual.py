"""Engine-level tests for the manual / evaluator provider archetype."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import AppConfig, ManualConfig, ManualRuleConfig, SyncConfig, TrustConfig, TwingateConfig
from src.engine import run_sync_cycle
from src.providers.base import ProviderDevice, ProviderPlugin
from src.providers.manual import ManualProvider
from src.twingate.models import TrustMutationResult, TwingateDevice


def _make_config(mode: str = "any", dry_run: bool = False, batch_size: int = 10) -> AppConfig:
    return AppConfig(
        twingate=TwingateConfig(tenant="t", api_key="k"),
        sync=SyncConfig(interval_seconds=60, dry_run=dry_run, batch_size=batch_size),
        trust=TrustConfig(mode=mode, require_online=True, require_compliant=True, max_days_since_checkin=7),
        providers=[],
    )


def _tg_device(device_id: str = "dev-1", **fields) -> TwingateDevice:
    base = {"id": device_id, "isTrusted": False}
    base.update(fields)
    return TwingateDevice.model_validate(base)


def _make_tg_client(devices: list[TwingateDevice], trust_ok: bool = True) -> MagicMock:
    client = MagicMock()
    client.list_untrusted_devices = AsyncMock(return_value=devices)
    client.trust_device = AsyncMock(
        return_value=TrustMutationResult(ok=trust_ok, error=None if trust_ok else "fail")
    )
    return client


def _manual_provider(name: str, rules: list[ManualRuleConfig], match_mode: str = "all") -> ManualProvider:
    cfg = ManualConfig(type="manual", enabled=True, name=name, match_mode=match_mode, rules=rules)
    return ManualProvider(cfg)


class _InventoryMock(ProviderPlugin):
    """Inventory-archetype mock with a fixed serial-keyed device list."""

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


# ---------------------------------------------------------------------------
# Pure manual: no MDM providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_alone_trusts_matching_device() -> None:
    config = _make_config()
    devices = [_tg_device("d1", hostname="VDI-001", serialNumber="SN1")]
    tg = _make_tg_client(devices)
    provider = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [provider], tg)

    assert summary.total_trusted == 1
    tg.trust_device.assert_awaited_once()
    assert tg.trust_device.await_args.args[0] == "d1"


@pytest.mark.asyncio
async def test_manual_alone_does_not_trust_non_matching() -> None:
    config = _make_config()
    devices = [_tg_device("d1", hostname="PROD-001", serialNumber="SN1")]
    tg = _make_tg_client(devices)
    provider = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [provider], tg)

    assert summary.total_trusted == 0
    assert summary.total_no_match == 1
    tg.trust_device.assert_not_awaited()


# ---------------------------------------------------------------------------
# Mixed inventory + manual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_any_mode_trusts_when_only_manual_matches() -> None:
    """trust.mode=any + manual matches, inventory doesn't -> still trusted."""
    config = _make_config(mode="any")
    devices = [_tg_device("d1", hostname="VDI-001", serialNumber="SN-NOMATCH")]
    tg = _make_tg_client(devices)

    inventory = _InventoryMock("mdm-x", devices=[])
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [inventory, manual], tg)

    assert summary.total_trusted == 1


@pytest.mark.asyncio
async def test_all_mode_requires_both_to_vote_yes() -> None:
    """trust.mode=all + only manual matches -> NOT trusted."""
    config = _make_config(mode="all")
    devices = [_tg_device("d1", hostname="VDI-001", serialNumber="SN-NOMATCH")]
    tg = _make_tg_client(devices)

    inventory = _InventoryMock("mdm-x", devices=[])
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [inventory, manual], tg)

    assert summary.total_trusted == 0
    # Device was matched by manual but failed `all` mode because inventory had no record
    assert summary.total_skipped == 1


@pytest.mark.asyncio
async def test_all_mode_both_matching_trusts() -> None:
    config = _make_config(mode="all")
    devices = [_tg_device("d1", hostname="VDI-001", serialNumber="SN1")]
    tg = _make_tg_client(devices)

    matching_inventory_dev = ProviderDevice(
        serial_number="SN1",
        is_online=True,
        is_compliant=True,
        last_seen=datetime.now(tz=UTC) - timedelta(hours=1),
    )
    inventory = _InventoryMock("mdm-x", devices=[matching_inventory_dev])
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [inventory, manual], tg)

    assert summary.total_trusted == 1


# ---------------------------------------------------------------------------
# Devices without a serial number -- should still go through evaluators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_serial_device_matched_by_manual_is_trusted() -> None:
    """The pre-fix code skipped no-serial devices entirely. Manual rules must
    still see them."""
    config = _make_config()
    devices = [_tg_device("d1", hostname="VDI-001")]  # no serialNumber
    tg = _make_tg_client(devices)
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [manual], tg)

    assert summary.total_trusted == 1
    tg.trust_device.assert_awaited_once()
    assert tg.trust_device.await_args.args[0] == "d1"


@pytest.mark.asyncio
async def test_no_serial_device_with_no_evaluators_still_no_match() -> None:
    """No serial + only inventory providers -> still no_match (existing behaviour)."""
    config = _make_config()
    devices = [_tg_device("d1", hostname="VDI-001")]
    tg = _make_tg_client(devices)
    inventory = _InventoryMock("mdm-x", devices=[])

    summary = await run_sync_cycle(config, [inventory], tg)

    assert summary.total_no_match == 1
    assert summary.total_trusted == 0


# ---------------------------------------------------------------------------
# Stats accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_provider_appears_in_summary_stats() -> None:
    config = _make_config()
    devices = [_tg_device("d1", hostname="VDI-001"), _tg_device("d2", hostname="PROD-001")]
    tg = _make_tg_client(devices)
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [manual], tg)

    names = [s.name for s in summary.provider_stats]
    assert "vdi" in names
    vdi_stats = next(s for s in summary.provider_stats if s.name == "vdi")
    assert vdi_stats.available is True
    assert vdi_stats.devices_fetched == 0  # evaluators don't fetch
    assert vdi_stats.matches_found == 1     # one device matched


# ---------------------------------------------------------------------------
# trust.mode=all: symmetric failure path (inventory yes, evaluator no)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_mode_inventory_only_match_is_skipped() -> None:
    """trust.mode=all + inventory matches + evaluator does NOT → skipped, not trusted."""
    config = _make_config(mode="all")
    devices = [_tg_device("d1", hostname="PROD-001", serialNumber="SN1")]
    tg = _make_tg_client(devices)

    matching_inventory_dev = ProviderDevice(
        serial_number="SN1",
        is_online=True,
        is_compliant=True,
        last_seen=datetime.now(tz=UTC) - timedelta(hours=1),
    )
    inventory = _InventoryMock("mdm-x", devices=[matching_inventory_dev])
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [inventory, manual], tg)

    assert summary.total_trusted == 0
    assert summary.total_skipped == 1


# ---------------------------------------------------------------------------
# Evaluator exception isolation — design rule: provider failure is non-fatal.
# ---------------------------------------------------------------------------


class _RaisingEvaluator(ProviderPlugin):
    """Evaluator that raises on every call. Used to verify the engine's
    per-device try/except keeps the cycle going."""

    @property
    def name(self) -> str:
        return "raising"

    async def authenticate(self) -> None:
        pass

    async def list_devices(self) -> list[ProviderDevice]:
        return []

    def determine_compliance(self, device: dict) -> bool:
        return False

    async def evaluate_device(self, tg_device: TwingateDevice) -> ProviderDevice | None:
        raise RuntimeError("evaluator boom")


@pytest.mark.asyncio
async def test_evaluator_exception_does_not_abort_cycle() -> None:
    """A raising evaluator must not crash the per-device loop; its error
    is logged, treated as no-claim, and the next evaluator still runs."""
    config = _make_config()
    devices = [_tg_device("d1", hostname="VDI-001")]
    tg = _make_tg_client(devices)

    raising = _RaisingEvaluator()
    surviving = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    summary = await run_sync_cycle(config, [raising, surviving], tg)

    # Surviving evaluator should still match → device trusted.
    assert summary.total_trusted == 1
    # Raising evaluator's error was recorded.
    raising_stats = next(s for s in summary.provider_stats if s.name == "raising")
    assert raising_stats.errors == 1


# ---------------------------------------------------------------------------
# Batched trust mutations — concurrency cap and error isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutation_fanout_capped_by_batch_size() -> None:
    """20 devices, batch_size=5 → at most 5 mutations in flight simultaneously,
    AND the fan-out must actually exercise concurrency (>1 in flight) so we
    know batching is real, not accidentally serial."""
    config = _make_config(batch_size=5)
    devices = [_tg_device(f"d{i}", hostname=f"VDI-{i:03d}") for i in range(20)]
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def fake_trust(device_id: str, contributors=None) -> TrustMutationResult:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return TrustMutationResult(ok=True)

    tg = MagicMock()
    tg.list_untrusted_devices = AsyncMock(return_value=devices)
    tg.trust_device = AsyncMock(side_effect=fake_trust)

    summary = await run_sync_cycle(config, [manual], tg)

    assert summary.total_trusted == 20
    assert max_in_flight <= 5
    # Batching must actually exercise concurrency or this test is meaningless.
    assert max_in_flight >= 2


@pytest.mark.asyncio
async def test_one_mutation_failure_does_not_abort_batch() -> None:
    """One trust_device call raises → others still trust; total_errors == 1."""
    config = _make_config(batch_size=10)
    devices = [_tg_device(f"d{i}", hostname=f"VDI-{i:03d}") for i in range(5)]
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])

    async def trust_with_one_failure(device_id: str, contributors=None) -> TrustMutationResult:
        if device_id == "d2":
            raise RuntimeError("boom")
        return TrustMutationResult(ok=True)

    tg = MagicMock()
    tg.list_untrusted_devices = AsyncMock(return_value=devices)
    tg.trust_device = AsyncMock(side_effect=trust_with_one_failure)

    summary = await run_sync_cycle(config, [manual], tg)

    assert summary.total_trusted == 4
    assert summary.total_errors == 1


@pytest.mark.asyncio
async def test_dry_run_path_does_not_call_trust_device() -> None:
    config = _make_config(dry_run=True, batch_size=5)
    devices = [_tg_device(f"d{i}", hostname=f"VDI-{i:03d}") for i in range(3)]
    manual = _manual_provider("vdi", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])
    tg = _make_tg_client(devices)

    summary = await run_sync_cycle(config, [manual], tg)

    assert summary.total_trusted == 3
    tg.trust_device.assert_not_awaited()
