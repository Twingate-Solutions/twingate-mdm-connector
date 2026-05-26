"""Unit tests for ManualProvider.

These tests construct ManualConfig / ManualRuleConfig instances directly and
feed TwingateDevice fixtures through ``ManualProvider.evaluate_device``.  No
HTTP or asyncio.gather involved.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.config import ManualConfig, ManualRuleConfig
from src.providers.manual import ManualProvider
from src.twingate.models import TwingateDevice


def _device(**fields) -> TwingateDevice:
    base = {"id": "dev-1"}
    base.update(fields)
    return TwingateDevice.model_validate(base)


def _make_provider(name: str, match_mode: str, rules: list[ManualRuleConfig]) -> ManualProvider:
    cfg = ManualConfig(type="manual", enabled=True, name=name, match_mode=match_mode, rules=rules)
    return ManualProvider(cfg)


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_name_matches_config() -> None:
    p = _make_provider("vdi-pool-a", "all", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])
    assert p.name == "vdi-pool-a"


@pytest.mark.asyncio
async def test_authenticate_is_noop() -> None:
    p = _make_provider("x", "all", [
        ManualRuleConfig(field="hostname", check="equals", value="x"),
    ])
    await p.authenticate()


@pytest.mark.asyncio
async def test_list_devices_returns_empty() -> None:
    p = _make_provider("x", "all", [
        ManualRuleConfig(field="hostname", check="equals", value="x"),
    ])
    assert await p.list_devices() == []


# ---------------------------------------------------------------------------
# Per-check behaviour (case-insensitive default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check,rule_value,device_value,expected",
    [
        ("equals",      "Windows 10", "windows 10", True),
        ("equals",      "Windows 10", "Windows 11", False),
        ("starts_with", "VDI-",       "vdi-001",    True),
        ("starts_with", "VDI-",       "PROD-001",   False),
        ("ends_with",   "@corp.com",  "u@CORP.COM", True),
        ("ends_with",   "@corp.com",  "u@other.com",False),
        ("contains",    "POOL",       "vdi-pool-a", True),
        ("contains",    "POOL",       "vdi-001",    False),
        ("regex",       r"^vdi-\d+$", "VDI-001",    True),
        ("regex",       r"^vdi-\d+$", "PROD-1",     False),
    ],
)
async def test_single_rule_checks_case_insensitive(check, rule_value, device_value, expected) -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="hostname", check=check, value=rule_value),
    ])
    result = await p.evaluate_device(_device(hostname=device_value))
    assert (result is not None) is expected


@pytest.mark.asyncio
async def test_regex_can_opt_into_case_sensitive() -> None:
    # Python's ``re`` requires the scoped-group form ``(?-i:...)`` to disable a
    # globally-enabled flag — the bare ``(?-i)`` token is rejected at compile
    # time.  This regex matches case-sensitively despite the provider passing
    # ``re.IGNORECASE`` at compile time.
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="hostname", check="regex", value=r"(?-i:^VDI-\d+$)"),
    ])
    assert await p.evaluate_device(_device(hostname="vdi-001")) is None
    assert await p.evaluate_device(_device(hostname="VDI-001")) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check,values,device_value,expected",
    [
        ("in",     ["LAPTOP", "DESKTOP"], "laptop", True),
        ("in",     ["LAPTOP", "DESKTOP"], "mobile", False),
        ("not_in", ["LAPTOP", "DESKTOP"], "mobile", True),
        ("not_in", ["LAPTOP", "DESKTOP"], "laptop", False),
        ("in",     [],                    "x",      False),
    ],
)
async def test_in_not_in_checks(check, values, device_value, expected) -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="device_type", check=check, value=values),
    ])
    result = await p.evaluate_device(_device(deviceType=device_value))
    assert (result is not None) is expected


# ---------------------------------------------------------------------------
# Null handling — None field never matches any check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check,value",
    [
        ("equals",      "x"),
        ("starts_with", "x"),
        ("ends_with",   "x"),
        ("contains",    "x"),
        ("regex",       r".*"),
        ("in",          ["x"]),
        ("not_in",      ["x"]),
    ],
)
async def test_null_field_never_matches(check, value) -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="hostname", check=check, value=value),
    ])
    assert await p.evaluate_device(_device()) is None


# ---------------------------------------------------------------------------
# Field accessors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_email_field_reads_user_dot_email() -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="user_email", check="ends_with", value="@example.com"),
    ])
    device = _device(user={"email": "a@example.com"})
    assert await p.evaluate_device(device) is not None


@pytest.mark.asyncio
async def test_user_email_handles_missing_user() -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="user_email", check="ends_with", value="@example.com"),
    ])
    device = _device(user=None)
    assert await p.evaluate_device(device) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", [
    "hostname", "serial_number", "os_name", "os_version",
    "name", "username", "device_type",
])
async def test_each_top_level_field_readable(field_name) -> None:
    """Smoke test: each declared field is actually wired to the right accessor."""
    alias_map = {
        "hostname": "hostname",
        "serial_number": "serialNumber",
        "os_name": "osName",
        "os_version": "osVersion",
        "name": "name",
        "username": "username",
        "device_type": "deviceType",
    }
    p = _make_provider("p", "all", [
        ManualRuleConfig(field=field_name, check="equals", value="match-me"),
    ])
    device = _device(**{alias_map[field_name]: "MATCH-ME"})
    assert await p.evaluate_device(device) is not None


# ---------------------------------------------------------------------------
# Match modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_mode_all_requires_every_rule() -> None:
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
        ManualRuleConfig(field="os_name",  check="equals",      value="Windows 10"),
    ])
    assert await p.evaluate_device(_device(hostname="VDI-001", osName="Windows 10")) is not None
    assert await p.evaluate_device(_device(hostname="VDI-001", osName="macOS")) is None
    assert await p.evaluate_device(_device(hostname="PROD-001", osName="macOS")) is None


@pytest.mark.asyncio
async def test_match_mode_any_requires_one_rule() -> None:
    p = _make_provider("p", "any", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
        ManualRuleConfig(field="os_name",  check="equals",      value="Windows 10"),
    ])
    assert await p.evaluate_device(_device(hostname="VDI-001", osName="macOS")) is not None
    assert await p.evaluate_device(_device(hostname="PROD-001", osName="Windows 10")) is not None
    assert await p.evaluate_device(_device(hostname="PROD-001", osName="macOS")) is None


@pytest.mark.asyncio
async def test_match_mode_any_short_circuits_after_first_match() -> None:
    """In `any` mode evaluation stops at the first matching rule, so
    `matched_fields` contains exactly that one field."""
    p = _make_provider("p", "any", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
        ManualRuleConfig(field="os_name",  check="equals",      value="Windows 10"),
    ])
    result = await p.evaluate_device(_device(hostname="VDI-001", osName="Windows 10"))
    assert result is not None
    assert result.raw["matched_fields"] == ["hostname"]


@pytest.mark.asyncio
async def test_match_mode_all_records_every_matched_field() -> None:
    """In `all` mode every rule must pass, and `matched_fields` lists each
    rule's field in declaration order."""
    p = _make_provider("p", "all", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
        ManualRuleConfig(field="os_name",  check="equals",      value="Windows 10"),
    ])
    result = await p.evaluate_device(_device(hostname="VDI-001", osName="Windows 10"))
    assert result is not None
    assert result.raw["matched_fields"] == ["hostname", "os_name"]


# ---------------------------------------------------------------------------
# Synthesised ProviderDevice shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesised_provider_device_shape() -> None:
    p = _make_provider("vdi-pool-a", "all", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])
    device = _device(hostname="VDI-001", serialNumber="vdi-serial", osName="Windows 10")
    result = await p.evaluate_device(device)
    assert result is not None
    assert result.is_compliant is True
    assert result.is_online is True
    assert result.serial_number == "VDI-SERIAL"  # normalised .strip().upper()
    assert result.hostname == "VDI-001"
    assert result.os_name == "Windows 10"
    now = datetime.now(tz=UTC)
    assert result.last_seen is not None
    assert (now - result.last_seen) < timedelta(seconds=5)
    assert result.raw["rule_set"] == "vdi-pool-a"
    assert isinstance(result.raw["matched_fields"], list)


@pytest.mark.asyncio
async def test_no_serial_device_still_synthesises() -> None:
    p = _make_provider("vdi-by-host", "all", [
        ManualRuleConfig(field="hostname", check="starts_with", value="VDI-"),
    ])
    device = _device(hostname="VDI-001")
    result = await p.evaluate_device(device)
    assert result is not None
    assert result.serial_number == ""
