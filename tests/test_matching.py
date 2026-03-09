"""Unit tests for src/matching.py."""

from datetime import UTC, datetime, timedelta

from src.matching import (
    build_provider_index,
    evaluate_trust,
    is_device_recent,
    normalize_serial,
)
from src.providers.base import ProviderDevice
from src.twingate.models import TwingateDevice


# ---------------------------------------------------------------------------
# normalize_serial
# ---------------------------------------------------------------------------


def test_normalize_serial_strips_and_uppercases() -> None:
    assert normalize_serial("  abc123  ") == "ABC123"


def test_normalize_serial_already_clean() -> None:
    assert normalize_serial("ABC123") == "ABC123"


def test_normalize_serial_none_returns_none() -> None:
    assert normalize_serial(None) is None


def test_normalize_serial_empty_string_returns_none() -> None:
    assert normalize_serial("") is None


def test_normalize_serial_whitespace_only_returns_none() -> None:
    assert normalize_serial("   ") is None


def test_normalize_serial_mixed_case() -> None:
    assert normalize_serial("AbCdEf") == "ABCDEF"


# ---------------------------------------------------------------------------
# build_provider_index
# ---------------------------------------------------------------------------


def _make_device(serial: str, **kwargs) -> ProviderDevice:
    return ProviderDevice(serial_number=serial, **kwargs)


def test_build_provider_index_basic() -> None:
    devices = [
        _make_device("abc123"),
        _make_device("def456"),
    ]
    index = build_provider_index(devices)
    assert "ABC123" in index
    assert "DEF456" in index


def test_build_provider_index_normalizes_keys() -> None:
    devices = [_make_device("  abc123  ")]
    index = build_provider_index(devices)
    assert "ABC123" in index
    assert "  abc123  " not in index


def test_build_provider_index_skips_empty_serial() -> None:
    devices = [
        _make_device(""),
        _make_device("valid"),
    ]
    index = build_provider_index(devices)
    assert len(index) == 1
    assert "VALID" in index


def test_build_provider_index_first_wins_on_duplicate() -> None:
    d1 = _make_device("ABC", hostname="first")
    d2 = _make_device("abc", hostname="second")
    index = build_provider_index([d1, d2])
    assert index["ABC"].hostname == "first"


def test_build_provider_index_empty_list() -> None:
    assert build_provider_index([]) == {}


# ---------------------------------------------------------------------------
# is_device_recent
# ---------------------------------------------------------------------------


def _device_seen_days_ago(days: float) -> ProviderDevice:
    return ProviderDevice(
        serial_number="X",
        last_seen=datetime.now(tz=UTC) - timedelta(days=days),
    )


def test_is_device_recent_within_window() -> None:
    device = _device_seen_days_ago(3)
    assert is_device_recent(device, max_days=7) is True


def test_is_device_recent_exactly_at_boundary() -> None:
    # 7 days ago exactly — should be recent (>= cutoff)
    device = _device_seen_days_ago(7)
    assert is_device_recent(device, max_days=7) is True


def test_is_device_recent_just_outside_window() -> None:
    device = _device_seen_days_ago(7.01)
    assert is_device_recent(device, max_days=7) is False


def test_is_device_recent_no_last_seen() -> None:
    device = ProviderDevice(serial_number="X", last_seen=None)
    assert is_device_recent(device, max_days=7) is False


def test_is_device_recent_naive_datetime_treated_as_utc() -> None:
    # Naive datetime (no tzinfo) — should still work
    device = ProviderDevice(
        serial_number="X",
        last_seen=datetime.utcnow() - timedelta(days=1),
    )
    assert is_device_recent(device, max_days=7) is True


# ---------------------------------------------------------------------------
# evaluate_trust — helpers
# ---------------------------------------------------------------------------


def _tg_device(serial: str = "ABC123") -> TwingateDevice:
    return TwingateDevice(id="tg-1", serialNumber=serial)


def _passing_device(serial: str = "ABC123") -> ProviderDevice:
    return ProviderDevice(
        serial_number=serial,
        is_online=True,
        is_compliant=True,
        last_seen=datetime.now(tz=UTC) - timedelta(hours=1),
    )


def _failing_device(serial: str = "ABC123") -> ProviderDevice:
    return ProviderDevice(
        serial_number=serial,
        is_online=False,
        is_compliant=False,
        last_seen=datetime.now(tz=UTC) - timedelta(days=30),
    )


_TRUST_KWARGS = dict(
    require_online=True,
    require_compliant=True,
    max_days_since_checkin=7,
)


# ---------------------------------------------------------------------------
# evaluate_trust — mode=any
# ---------------------------------------------------------------------------


def test_evaluate_trust_any_single_passing_provider() -> None:
    tg = _tg_device()
    results = {"ninjaone": _passing_device()}
    trust, contributors = evaluate_trust(tg, results, mode="any", **_TRUST_KWARGS)
    assert trust is True
    assert contributors == ["ninjaone"]


def test_evaluate_trust_any_one_passing_one_failing() -> None:
    tg = _tg_device()
    results = {
        "ninjaone": _passing_device(),
        "sophos": _failing_device(),
    }
    trust, contributors = evaluate_trust(tg, results, mode="any", **_TRUST_KWARGS)
    assert trust is True
    assert "ninjaone" in contributors
    assert "sophos" not in contributors


def test_evaluate_trust_any_all_failing() -> None:
    tg = _tg_device()
    results = {"ninjaone": _failing_device(), "sophos": _failing_device()}
    trust, _ = evaluate_trust(tg, results, mode="any", **_TRUST_KWARGS)
    assert trust is False


def test_evaluate_trust_any_no_match_in_provider() -> None:
    tg = _tg_device()
    results = {"ninjaone": None}
    trust, contributors = evaluate_trust(tg, results, mode="any", **_TRUST_KWARGS)
    assert trust is False
    assert contributors == []


# ---------------------------------------------------------------------------
# evaluate_trust — mode=all
# ---------------------------------------------------------------------------


def test_evaluate_trust_all_all_passing() -> None:
    tg = _tg_device()
    results = {"ninjaone": _passing_device(), "sophos": _passing_device()}
    trust, contributors = evaluate_trust(tg, results, mode="all", **_TRUST_KWARGS)
    assert trust is True
    assert set(contributors) == {"ninjaone", "sophos"}


def test_evaluate_trust_all_one_failing() -> None:
    tg = _tg_device()
    results = {"ninjaone": _passing_device(), "sophos": _failing_device()}
    trust, _ = evaluate_trust(tg, results, mode="all", **_TRUST_KWARGS)
    assert trust is False


def test_evaluate_trust_all_one_missing() -> None:
    tg = _tg_device()
    results = {"ninjaone": _passing_device(), "sophos": None}
    trust, _ = evaluate_trust(tg, results, mode="all", **_TRUST_KWARGS)
    assert trust is False


def test_evaluate_trust_all_empty_providers() -> None:
    tg = _tg_device()
    trust, contributors = evaluate_trust(tg, {}, mode="all", **_TRUST_KWARGS)
    assert trust is False
    assert contributors == []


# ---------------------------------------------------------------------------
# evaluate_trust — require_online / require_compliant flags
# ---------------------------------------------------------------------------


def test_evaluate_trust_offline_device_skipped_when_require_online() -> None:
    tg = _tg_device()
    device = ProviderDevice(
        serial_number="ABC123",
        is_online=False,
        is_compliant=True,
        last_seen=datetime.now(tz=UTC),
    )
    trust, _ = evaluate_trust(
        tg, {"provider": device}, mode="any",
        require_online=True, require_compliant=False, max_days_since_checkin=7,
    )
    assert trust is False


def test_evaluate_trust_offline_device_ok_when_not_require_online() -> None:
    tg = _tg_device()
    device = ProviderDevice(
        serial_number="ABC123",
        is_online=False,
        is_compliant=True,
        last_seen=datetime.now(tz=UTC),
    )
    trust, _ = evaluate_trust(
        tg, {"provider": device}, mode="any",
        require_online=False, require_compliant=True, max_days_since_checkin=7,
    )
    assert trust is True


def test_evaluate_trust_non_compliant_skipped_when_require_compliant() -> None:
    tg = _tg_device()
    device = ProviderDevice(
        serial_number="ABC123",
        is_online=True,
        is_compliant=False,
        last_seen=datetime.now(tz=UTC),
    )
    trust, _ = evaluate_trust(
        tg, {"provider": device}, mode="any",
        require_online=False, require_compliant=True, max_days_since_checkin=7,
    )
    assert trust is False
