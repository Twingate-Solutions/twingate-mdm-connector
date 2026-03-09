"""Unit tests for src/providers/sophos.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import SophosConfig
from src.providers.sophos import SophosProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> SophosConfig:
    return SophosConfig(
        type="sophos",
        enabled=True,
        client_id="test-client-id",
        client_secret="test-client-secret",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _token_response() -> MagicMock:
    return _make_response({"access_token": "sophos-token", "expires_in": 3600})


def _whoami_response() -> MagicMock:
    return _make_response(
        {
            "id": "tenant-uuid-123",
            "idType": "tenant",
            "apiHosts": {
                "global": "https://api.central.sophos.com",
                "dataRegion": "https://api-us01.central.sophos.com",
            },
        }
    )


def _endpoint(
    endpoint_id: str = "ep-1",
    hostname: str = "workstation-01",
    serial: str = "SOPHOS-SN-001",
    health_overall: str = "good",
    services_status: str = "good",
    last_seen: str = "2024-01-15T10:00:00Z",
) -> dict:
    return {
        "id": endpoint_id,
        "hostname": hostname,
        "serialNumber": serial,
        "health": {
            "overall": health_overall,
            "threats": {"status": "good"},
            "services": {"status": services_status},
        },
        "os": {"platform": "windows", "majorVersion": 10},
        "lastSeenAt": last_seen,
    }


def _endpoints_page(
    items: list[dict],
    next_key: str | None = None,
) -> MagicMock:
    pages: dict = {"size": 500}
    if next_key:
        pages["nextKey"] = next_key
    return _make_response({"items": items, "pages": pages})


# ---------------------------------------------------------------------------
# authenticate — two-hop flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_performs_two_hop_flow() -> None:
    provider = SophosProvider(_make_config())
    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _token_response() if call_count == 1 else _whoami_response()

    with patch("src.providers.sophos.request_with_retry", new=_mock):
        await provider.authenticate()

    assert call_count == 2
    assert provider._token_cache.token == "sophos-token"
    assert provider._tenant_id == "tenant-uuid-123"
    assert provider._api_base == "https://api-us01.central.sophos.com"


@pytest.mark.asyncio
async def test_authenticate_skips_when_token_and_tenant_fresh() -> None:
    provider = SophosProvider(_make_config())
    provider._token_cache.set("existing-token", 3600)
    provider._tenant_id = "some-tenant"
    provider._api_base = "https://api-us01.central.sophos.com"

    mock = AsyncMock()
    with patch("src.providers.sophos.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_raises_on_token_error() -> None:
    provider = SophosProvider(_make_config())
    err_resp = _make_response({"error": "invalid_client"}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch(
        "src.providers.sophos.request_with_retry", new=AsyncMock(return_value=err_resp)
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices — requires authenticate first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_raises_if_not_authenticated() -> None:
    provider = SophosProvider(_make_config())
    with pytest.raises(RuntimeError, match="authenticate\\(\\) must be called first"):
        await provider.list_devices()


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = SophosProvider(_make_config())
    provider._token_cache.set("sophos-token", 3600)
    provider._tenant_id = "tenant-uuid"
    provider._api_base = "https://api-us01.central.sophos.com"

    resp = _endpoints_page([_endpoint("ep-1", serial="SN-AAA"), _endpoint("ep-2", serial="SN-BBB")])

    with patch("src.providers.sophos.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    serials = {d.serial_number for d in result}
    assert "SN-AAA" in serials
    assert "SN-BBB" in serials


@pytest.mark.asyncio
async def test_list_devices_skips_endpoint_with_no_serial() -> None:
    provider = SophosProvider(_make_config())
    provider._token_cache.set("sophos-token", 3600)
    provider._tenant_id = "tenant-uuid"
    provider._api_base = "https://api-us01.central.sophos.com"

    no_serial = {"id": "ep-x", "hostname": "ghost", "health": {"overall": "good"}}
    resp = _endpoints_page([no_serial])

    with patch("src.providers.sophos.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = SophosProvider(_make_config())
    provider._token_cache.set("sophos-token", 3600)
    provider._tenant_id = "tenant-uuid"
    provider._api_base = "https://api-us01.central.sophos.com"

    ep = _endpoint(serial="  abc-def  ")
    resp = _endpoints_page([ep])

    with patch("src.providers.sophos.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "ABC-DEF"


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_follows_next_key_cursor() -> None:
    provider = SophosProvider(_make_config())
    provider._token_cache.set("sophos-token", 3600)
    provider._tenant_id = "tenant-uuid"
    provider._api_base = "https://api-us01.central.sophos.com"

    page1 = _endpoints_page([_endpoint("ep-1", serial="SN-1")], next_key="cursor-abc")
    page2 = _endpoints_page([_endpoint("ep-2", serial="SN-2")])

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return page1 if call_count == 1 else page2

    with patch("src.providers.sophos.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == 2


# ---------------------------------------------------------------------------
# serial number field fallbacks
# ---------------------------------------------------------------------------


def test_build_device_uses_top_level_serial() -> None:
    provider = SophosProvider(_make_config())
    raw = {"id": "x", "serialNumber": "TOP-SERIAL", "health": {}}
    device = provider._build_device(raw)
    assert device.serial_number == "TOP-SERIAL"


def test_build_device_falls_back_to_os_serial() -> None:
    provider = SophosProvider(_make_config())
    raw = {"id": "x", "os": {"serialNumber": "OS-SERIAL"}, "health": {}}
    device = provider._build_device(raw)
    assert device.serial_number == "OS-SERIAL"


def test_build_device_falls_back_to_metadata_serial() -> None:
    provider = SophosProvider(_make_config())
    raw = {"id": "x", "metadata": {"computerSerial": "META-SERIAL"}, "health": {}}
    device = provider._build_device(raw)
    assert device.serial_number == "META-SERIAL"


def test_build_device_empty_serial_when_no_field_found() -> None:
    provider = SophosProvider(_make_config())
    raw = {"id": "x", "hostname": "ghost", "health": {}}
    device = provider._build_device(raw)
    assert device.serial_number == ""


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_good_health() -> None:
    provider = SophosProvider(_make_config())
    raw = {"health": {"overall": "good"}}
    assert provider.determine_compliance(raw) is True


def test_compliance_bad_health() -> None:
    provider = SophosProvider(_make_config())
    raw = {"health": {"overall": "bad"}}
    assert provider.determine_compliance(raw) is False


def test_compliance_suspicious_health() -> None:
    provider = SophosProvider(_make_config())
    raw = {"health": {"overall": "suspicious"}}
    assert provider.determine_compliance(raw) is False


def test_compliance_missing_health_is_non_compliant() -> None:
    provider = SophosProvider(_make_config())
    assert provider.determine_compliance({}) is False


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


def test_online_when_services_status_good() -> None:
    provider = SophosProvider(_make_config())
    raw = {
        "health": {"overall": "good", "services": {"status": "good"}},
        "lastSeenAt": "2024-01-01T00:00:00Z",
    }
    device = provider._build_device(raw)
    assert device.is_online is True


def test_offline_when_services_status_not_good() -> None:
    provider = SophosProvider(_make_config())
    raw = {
        "health": {"overall": "bad", "services": {"status": "bad"}},
        "lastSeenAt": "2024-01-01T00:00:00Z",
    }
    device = provider._build_device(raw)
    assert device.is_online is False


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert SophosProvider(_make_config()).name == "sophos"
