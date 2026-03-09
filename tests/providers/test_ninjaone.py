"""Unit tests for src/providers/ninjaone.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import NinjaOneConfig
from src.providers.ninjaone import NinjaOneProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(region: str = "app") -> NinjaOneConfig:
    return NinjaOneConfig(
        type="ninjaone",
        enabled=True,
        region=region,
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
    return _make_response({"access_token": "test-token", "expires_in": 3600})


def _device(
    device_id: int = 1,
    serial: str = "SN-1234",
    offline: bool = False,
    threat_status: str = "GOOD",
    patch_status: str = "OK",
) -> dict:
    return {
        "id": device_id,
        "systemName": f"Device-{device_id}",
        "nodeClass": "WINDOWS_WORKSTATION",
        "offline": offline,
        "system": {"serialNumber": serial},
        "antivirus": {"threatStatus": threat_status},
        "patches": {"patchStatus": patch_status},
        "lastContact": 1_700_000_000,
    }


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_fetches_token() -> None:
    provider = NinjaOneProvider(_make_config())
    token_resp = _token_response()

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=token_resp)):
        await provider.authenticate()

    assert provider._token_cache.token == "test-token"
    assert not provider._token_cache.needs_refresh()


@pytest.mark.asyncio
async def test_authenticate_skips_when_token_fresh() -> None:
    provider = NinjaOneProvider(_make_config())
    # Pre-seed a fresh token
    provider._token_cache.set("existing-token", 3600)

    mock = AsyncMock()
    with patch("src.providers.ninjaone.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_raises_on_http_error() -> None:
    provider = NinjaOneProvider(_make_config())
    err_resp = _make_response({"error": "invalid_client"}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices — single page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    devices_resp = _make_response([_device(1, "ABC123"), _device(2, "DEF456")])

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=devices_resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    serials = {d.serial_number for d in result}
    assert "ABC123" in serials
    assert "DEF456" in serials


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    devices_resp = _make_response([_device(1, "  abc-123  ")])

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=devices_resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "ABC-123"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    no_serial = {
        "id": 99,
        "systemName": "Ghost",
        "offline": False,
        "system": {},
    }
    devices_resp = _make_response([no_serial])

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=devices_resp)):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_paginates_until_last_page() -> None:
    """Two full pages followed by a partial page → stops after third call."""
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    from src.providers.ninjaone import _PAGE_SIZE

    page1 = [_device(i, f"SN-{i}") for i in range(_PAGE_SIZE)]
    page2 = [_device(i + _PAGE_SIZE, f"SN-{i + _PAGE_SIZE}") for i in range(_PAGE_SIZE)]
    page3 = [_device(9999, "SN-LAST")]  # partial page → end

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(page1)
        if call_count == 2:
            return _make_response(page2)
        return _make_response(page3)

    with patch("src.providers.ninjaone.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 3
    assert len(result) == _PAGE_SIZE * 2 + 1


@pytest.mark.asyncio
async def test_list_devices_stops_on_empty_page() -> None:
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    empty_resp = _make_response([])

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=empty_resp)):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_clean_device() -> None:
    provider = NinjaOneProvider(_make_config())
    assert provider.determine_compliance(_device(threat_status="GOOD", patch_status="OK")) is True


def test_compliance_threat_detected() -> None:
    provider = NinjaOneProvider(_make_config())
    assert provider.determine_compliance(_device(threat_status="THREAT_DETECTED")) is False


def test_compliance_critical_patches() -> None:
    provider = NinjaOneProvider(_make_config())
    assert provider.determine_compliance(_device(patch_status="CRITICAL")) is False


def test_compliance_no_av_data_is_compliant() -> None:
    """Devices without AV data default to compliant (AV may not be installed)."""
    provider = NinjaOneProvider(_make_config())
    raw = {"id": 1, "offline": False}
    assert provider.determine_compliance(raw) is True


# ---------------------------------------------------------------------------
# online / offline status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_device_is_not_online() -> None:
    provider = NinjaOneProvider(_make_config())
    provider._token_cache.set("test-token", 3600)

    offline_device = _device(1, "OFFLINE-SN", offline=True)
    devices_resp = _make_response([offline_device])

    with patch("src.providers.ninjaone.request_with_retry", new=AsyncMock(return_value=devices_resp)):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# regional base URL construction
# ---------------------------------------------------------------------------


def test_eu_region_uses_eu_base_url() -> None:
    provider = NinjaOneProvider(_make_config(region="eu"))
    assert "eu.ninjarmm.com" in provider._base_url


def test_unknown_region_falls_back_to_custom_url() -> None:
    provider = NinjaOneProvider(_make_config(region="custom"))
    assert "custom.ninjarmm.com" in provider._base_url


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert NinjaOneProvider(_make_config()).name == "ninjaone"
