"""Unit tests for src/providers/datto.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import DattoConfig
from src.providers.datto import DattoProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(api_url: str = "https://pinotage-api.centrastage.net") -> DattoConfig:
    return DattoConfig(
        type="datto",
        enabled=True,
        api_url=api_url,
        api_key="datto-api-key",
        api_secret="datto-api-secret",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _token_response() -> MagicMock:
    return _make_response({"access_token": "datto-token", "expires_in": 360_000})


def _device(
    uid: str = "dev-uid-1",
    serial: str = "DATTO-SN-001",
    online: bool = True,
    patch_status: str = "FULLY_PATCHED",
    av_status: str = "PROTECTED",
    reboot_required: bool = False,
) -> dict:
    return {
        "uid": uid,
        "hostname": f"host-{uid}",
        "serialNumber": serial,
        "online": online,
        "operatingSystem": "Windows 10 Pro",
        "patchStatus": patch_status,
        "antivirusStatus": av_status,
        "rebootRequired": reboot_required,
        "lastSeen": "2024-01-15T10:00:00Z",
    }


def _devices_page(
    devices: list[dict],
    next_page_url: str | None = None,
) -> MagicMock:
    page_details: dict = {}
    if next_page_url:
        page_details["nextPageUrl"] = next_page_url
    return _make_response({"devices": devices, "pageDetails": page_details})


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_fetches_token() -> None:
    provider = DattoProvider(_make_config())
    token_resp = _token_response()

    with patch("src.providers.datto.request_with_retry", new=AsyncMock(return_value=token_resp)):
        await provider.authenticate()

    assert provider._token_cache.token == "datto-token"
    assert not provider._token_cache.needs_refresh()


@pytest.mark.asyncio
async def test_authenticate_skips_when_token_fresh() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("existing-token", 360_000)

    mock = AsyncMock()
    with patch("src.providers.datto.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_raises_on_http_error() -> None:
    provider = DattoProvider(_make_config())
    err_resp = _make_response({}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.datto.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices — single page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    resp = _devices_page([_device("d1", "SN-AAA"), _device("d2", "SN-BBB")])

    with patch("src.providers.datto.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-AAA", "SN-BBB"}


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    resp = _devices_page([_device(serial="  datto-sn-001  ")])

    with patch("src.providers.datto.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "DATTO-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    no_serial = {"uid": "ghost", "hostname": "ghost-host", "online": True}
    resp = _devices_page([no_serial])

    with patch("src.providers.datto.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_empty_response() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    with patch(
        "src.providers.datto.request_with_retry",
        new=AsyncMock(return_value=_devices_page([])),
    ):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — nextPageUrl pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_follows_next_page_url() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    page1 = _devices_page(
        [_device("d1", "SN-1")],
        next_page_url="https://pinotage-api.centrastage.net/api/v2/account/devices?page=2",
    )
    page2 = _devices_page([_device("d2", "SN-2")])

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return page1 if call_count == 1 else page2

    with patch("src.providers.datto.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-1", "SN-2"}


@pytest.mark.asyncio
async def test_list_devices_stops_when_next_page_url_absent() -> None:
    """No nextPageUrl in pageDetails → stop after first page."""
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _devices_page([_device("d1", "SN-1")])  # no nextPageUrl

    with patch("src.providers.datto.request_with_retry", new=_mock):
        await provider.list_devices()

    assert call_count == 1


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_fully_patched_protected() -> None:
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance(
        _device(patch_status="FULLY_PATCHED", av_status="PROTECTED", reboot_required=False)
    ) is True


def test_compliance_not_supported_not_applicable() -> None:
    """NOT_SUPPORTED patch / NOT_APPLICABLE AV → compliant (managed devices)."""
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance(
        _device(patch_status="NOT_SUPPORTED", av_status="NOT_APPLICABLE")
    ) is True


def test_compliance_unpatched() -> None:
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance(
        _device(patch_status="UNPATCHED", av_status="PROTECTED")
    ) is False


def test_compliance_av_not_protected() -> None:
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance(
        _device(patch_status="FULLY_PATCHED", av_status="NOT_PROTECTED")
    ) is False


def test_compliance_reboot_required() -> None:
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance(
        _device(patch_status="FULLY_PATCHED", av_status="PROTECTED", reboot_required=True)
    ) is False


def test_compliance_missing_status_fields_is_compliant() -> None:
    """Empty status strings don't trigger a failure (field absent = unknown)."""
    provider = DattoProvider(_make_config())
    assert provider.determine_compliance({"rebootRequired": False}) is True


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_online_device_is_online() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    with patch(
        "src.providers.datto.request_with_retry",
        new=AsyncMock(return_value=_devices_page([_device(online=True)])),
    ):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_offline_device_is_not_online() -> None:
    provider = DattoProvider(_make_config())
    provider._token_cache.set("datto-token", 360_000)

    with patch(
        "src.providers.datto.request_with_retry",
        new=AsyncMock(return_value=_devices_page([_device(online=False)])),
    ):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# token URL built from api_url
# ---------------------------------------------------------------------------


def test_token_url_uses_api_url() -> None:
    provider = DattoProvider(_make_config("https://merlot-api.centrastage.net"))
    assert provider._token_url == "https://merlot-api.centrastage.net/auth/oauth/token"


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert DattoProvider(_make_config()).name == "datto"
