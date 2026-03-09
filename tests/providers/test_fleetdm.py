"""Unit tests for src/providers/fleetdm.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import FleetDMConfig
from src.providers.fleetdm import FleetDMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(url: str = "https://fleet.example.com") -> FleetDMConfig:
    return FleetDMConfig(
        type="fleetdm",
        enabled=True,
        url=url,
        api_token="fleet-test-token",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _host_summary(
    host_id: int = 1,
    serial: str = "FD-SN-001",
    status: str = "online",
) -> dict:
    """Minimal host object as returned by the list endpoint."""
    return {
        "id": host_id,
        "hostname": f"host-{host_id}.local",
        "hardware_serial": serial,
        "status": status,
        "platform": "darwin",
        "os_version": "macOS 14.0",
        "last_enrolled_at": "2024-01-10T09:00:00Z",
    }


def _host_detail(
    host_id: int = 1,
    serial: str = "FD-SN-001",
    status: str = "online",
    policies: list[dict] | None = None,
) -> dict:
    """Host object as returned by the detail endpoint (includes policies)."""
    return {
        "id": host_id,
        "hostname": f"host-{host_id}.local",
        "hardware_serial": serial,
        "status": status,
        "platform": "darwin",
        "os_version": "macOS 14.0",
        "last_enrolled_at": "2024-01-10T09:00:00Z",
        "policies": policies if policies is not None else [],
    }


def _list_response(
    hosts: list[dict],
    has_next: bool = False,
) -> MagicMock:
    return _make_response(
        {"hosts": hosts, "meta": {"has_next_results": has_next}}
    )


def _detail_response(host: dict) -> MagicMock:
    return _make_response({"host": host})


# ---------------------------------------------------------------------------
# authenticate — no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_is_noop() -> None:
    provider = FleetDMProvider(_make_config())
    mock = AsyncMock()
    with patch("src.providers.fleetdm.request_with_retry", new=mock):
        await provider.authenticate()
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_devices — single page, single host
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_fetches_detail_for_policies() -> None:
    """list_devices calls list endpoint then detail endpoint per host."""
    provider = FleetDMProvider(_make_config())

    list_resp = _list_response([_host_summary(1, "SN-AAA")])
    detail_resp = _detail_response(
        _host_detail(1, "SN-AAA", policies=[{"id": 1, "response": "pass"}])
    )

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return list_resp if call_count == 1 else detail_resp

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == 1
    assert result[0].serial_number == "SN-AAA"
    assert result[0].is_compliant is True


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = FleetDMProvider(_make_config())

    list_resp = _list_response([_host_summary(1, "  fd-sn-001  ")])
    detail_resp = _detail_response(_host_detail(1, "  fd-sn-001  "))

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return list_resp if call_count == 1 else detail_resp

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result[0].serial_number == "FD-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_host_without_serial() -> None:
    provider = FleetDMProvider(_make_config())

    no_serial = {"id": 99, "hostname": "ghost", "status": "online"}
    list_resp = _list_response([no_serial])
    detail_resp = _detail_response({"id": 99, "hostname": "ghost"})

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return list_resp if call_count == 1 else detail_resp

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_returns_empty_when_no_hosts() -> None:
    provider = FleetDMProvider(_make_config())
    list_resp = _list_response([])

    with patch("src.providers.fleetdm.request_with_retry", new=AsyncMock(return_value=list_resp)):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_follows_has_next_results() -> None:
    provider = FleetDMProvider(_make_config())

    host1 = _host_summary(1, "SN-1")
    host2 = _host_summary(2, "SN-2")
    detail1 = _detail_response(_host_detail(1, "SN-1"))
    detail2 = _detail_response(_host_detail(2, "SN-2"))

    # page1 has_next=True, page2 has_next=False, then 2 detail calls
    responses = [
        _list_response([host1], has_next=True),
        _list_response([host2], has_next=False),
        detail1,
        detail2,
    ]
    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-1", "SN-2"}


# ---------------------------------------------------------------------------
# list_devices — detail fetch failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_fetch_failure_falls_back_to_list_data() -> None:
    """If the detail call fails, fall back to list data (no policies)."""
    provider = FleetDMProvider(_make_config())

    host = _host_summary(1, "SN-AAA")
    list_resp = _list_response([host])

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return list_resp
        # Detail call raises
        raise httpx.RequestError("connection refused")

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    # Device still present (from list data), but with no policy info
    assert len(result) == 1
    assert result[0].serial_number == "SN-AAA"
    assert result[0].is_compliant is True  # empty policies → compliant


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_all_policies_pass() -> None:
    provider = FleetDMProvider(_make_config())
    device = {"policies": [{"id": 1, "response": "pass"}, {"id": 2, "response": "pass"}]}
    assert provider.determine_compliance(device) is True


def test_compliance_one_policy_fails() -> None:
    provider = FleetDMProvider(_make_config())
    device = {"policies": [{"id": 1, "response": "pass"}, {"id": 2, "response": "fail"}]}
    assert provider.determine_compliance(device) is False


def test_compliance_no_policies_is_compliant() -> None:
    provider = FleetDMProvider(_make_config())
    assert provider.determine_compliance({}) is True
    assert provider.determine_compliance({"policies": []}) is True


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_online_host_is_online() -> None:
    provider = FleetDMProvider(_make_config())

    list_resp = _list_response([_host_summary(1, status="online")])
    detail_resp = _detail_response(_host_detail(1, status="online"))

    responses = [list_resp, detail_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_offline_host_is_not_online() -> None:
    provider = FleetDMProvider(_make_config())

    list_resp = _list_response([_host_summary(1, status="offline")])
    detail_resp = _detail_response(_host_detail(1, status="offline"))

    responses = [list_resp, detail_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.fleetdm.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert FleetDMProvider(_make_config()).name == "fleetdm"
