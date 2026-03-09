"""Unit tests for src/providers/mosyle.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import MosyleConfig
from src.providers.mosyle import MosyleProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(is_business: bool = False) -> MosyleConfig:
    return MosyleConfig(
        type="mosyle",
        enabled=True,
        is_business=is_business,
        access_token="mosyle-access-token",
        email="admin@example.com",
        password="secret",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _device(
    serial: str = "MOSYLE-SN-001",
    device_name: str = "MacBook Pro",
    status: str = "enrolled",
    os_version: str = "14.0",
    last_beat: str | int = "1700000000",
) -> dict:
    return {
        "serial_number": serial,
        "device_name": device_name,
        "status": status,
        "os_version": os_version,
        "date_last_beat": last_beat,
    }


def _mosyle_response(devices: list[dict], status: str = "OK") -> MagicMock:
    """Mosyle nested response format: response[0].devices."""
    return _make_response(
        {
            "status": status,
            "response": [{"status": "success", "qty": len(devices), "devices": devices}],
        }
    )


def _mosyle_empty_response() -> MagicMock:
    return _mosyle_response([])


# ---------------------------------------------------------------------------
# authenticate — no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_is_noop() -> None:
    provider = MosyleProvider(_make_config())
    mock = AsyncMock()
    with patch("src.providers.mosyle.request_with_retry", new=mock):
        await provider.authenticate()
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_devices — fetches both osx and ios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_fetches_osx_and_ios() -> None:
    """list_devices makes calls for both osx and ios OS types."""
    provider = MosyleProvider(_make_config())

    osx_device = _device("MAC-SN-001", "MacBook Pro")
    ios_device = _device("IOS-SN-001", "iPhone 15")

    # 4 calls: osx page1 (data), osx page2 (empty), ios page1 (data), ios page2 (empty)
    responses = [
        _mosyle_response([osx_device]),
        _mosyle_empty_response(),
        _mosyle_response([ios_device]),
        _mosyle_empty_response(),
    ]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.mosyle.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"MAC-SN-001", "IOS-SN-001"}


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = MosyleProvider(_make_config())

    responses = [
        _mosyle_response([_device("  mac-sn-001  ")]),
        _mosyle_empty_response(),
        _mosyle_empty_response(),
    ]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.mosyle.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result[0].serial_number == "MAC-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = MosyleProvider(_make_config())

    no_serial = {"device_name": "Ghost", "status": "enrolled"}
    responses = [
        _mosyle_response([no_serial]),
        _mosyle_empty_response(),
        _mosyle_empty_response(),
    ]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.mosyle.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_returns_empty_when_no_devices() -> None:
    provider = MosyleProvider(_make_config())

    with patch(
        "src.providers.mosyle.request_with_retry",
        new=AsyncMock(return_value=_mosyle_empty_response()),
    ):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_paginates_until_empty() -> None:
    """Mosyle pagination stops when a page returns an empty devices array."""
    provider = MosyleProvider(_make_config())

    osx_page1 = _mosyle_response([_device("SN-1"), _device("SN-2")])
    osx_page2 = _mosyle_response([_device("SN-3")])
    osx_page3 = _mosyle_empty_response()
    ios_empty = _mosyle_empty_response()

    responses = [osx_page1, osx_page2, osx_page3, ios_empty]
    idx = 0
    captured_bodies: list[dict] = []

    async def _mock(*args, **kwargs):
        nonlocal idx
        captured_bodies.append(kwargs.get("json", {}))
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.mosyle.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 3
    # Verify page numbers sent: 1, 2, 3 for osx; 1 for ios
    osx_pages = [b["options"]["page"] for b in captured_bodies if b.get("options", {}).get("os") == "osx"]
    assert osx_pages == [1, 2, 3]


# ---------------------------------------------------------------------------
# _extract_devices — response format handling
# ---------------------------------------------------------------------------


def test_extract_devices_nested_format() -> None:
    """Handles the nested response[0].devices format."""
    provider = MosyleProvider(_make_config())
    data = {"status": "OK", "response": [{"status": "success", "devices": [{"serial_number": "X"}]}]}
    assert len(provider._extract_devices(data)) == 1


def test_extract_devices_flat_format() -> None:
    """Handles a flat top-level devices key."""
    provider = MosyleProvider(_make_config())
    data = {"status": "OK", "devices": [{"serial_number": "X"}]}
    assert len(provider._extract_devices(data)) == 1


def test_extract_devices_empty_response() -> None:
    provider = MosyleProvider(_make_config())
    assert provider._extract_devices({}) == []
    assert provider._extract_devices({"response": [{}]}) == []


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_enrolled() -> None:
    provider = MosyleProvider(_make_config())
    assert provider.determine_compliance({"status": "enrolled"}) is True


def test_compliance_managed() -> None:
    provider = MosyleProvider(_make_config())
    assert provider.determine_compliance({"status": "managed"}) is True


def test_compliance_supervised() -> None:
    provider = MosyleProvider(_make_config())
    assert provider.determine_compliance({"status": "supervised"}) is True


def test_compliance_pending_is_non_compliant() -> None:
    provider = MosyleProvider(_make_config())
    assert provider.determine_compliance({"status": "pending"}) is False


def test_compliance_missing_status_is_non_compliant() -> None:
    provider = MosyleProvider(_make_config())
    assert provider.determine_compliance({}) is False


# ---------------------------------------------------------------------------
# base URL selection
# ---------------------------------------------------------------------------


def test_manager_uses_manager_base_url() -> None:
    from src.providers.mosyle import _MANAGER_BASE

    provider = MosyleProvider(_make_config(is_business=False))
    assert str(provider._client.base_url).rstrip("/") == _MANAGER_BASE.rstrip("/")


def test_business_uses_business_base_url() -> None:
    from src.providers.mosyle import _BUSINESS_BASE

    provider = MosyleProvider(_make_config(is_business=True))
    assert str(provider._client.base_url).rstrip("/") == _BUSINESS_BASE.rstrip("/")


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert MosyleProvider(_make_config()).name == "mosyle"
