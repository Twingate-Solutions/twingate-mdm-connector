"""Unit tests for src/twingate/client.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.twingate.client import TwingateClient
from src.twingate.models import TwingateDevice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(body: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _devices_page(
    devices: list[dict],
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict:
    """Build a mock GraphQL response for the GetUntrustedDevices query."""
    return {
        "data": {
            "devices": {
                "pageInfo": {
                    "hasNextPage": has_next_page,
                    "endCursor": end_cursor,
                },
                "edges": [{"node": d} for d in devices],
            }
        }
    }


def _trust_response(ok: bool, error: str | None = None) -> dict:
    return {
        "data": {
            "deviceUpdate": {
                "ok": ok,
                "error": error,
                "entity": {"id": "dev-1", "name": "My Mac", "isTrusted": ok},
            }
        }
    }


# ---------------------------------------------------------------------------
# list_untrusted_devices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_untrusted_devices_single_page() -> None:
    device_data = {"id": "dev-1", "serialNumber": "ABC123", "isTrusted": False}
    response = _make_response(_devices_page([device_data]))

    with patch("src.twingate.client.request_with_retry", new=AsyncMock(return_value=response)):
        async with TwingateClient("tenant", "key") as client:
            devices = await client.list_untrusted_devices()

    assert len(devices) == 1
    assert devices[0].id == "dev-1"
    assert devices[0].serial_number == "ABC123"


@pytest.mark.asyncio
async def test_list_untrusted_devices_two_pages() -> None:
    page1_device = {"id": "dev-1", "serialNumber": "AAA", "isTrusted": False}
    page2_device = {"id": "dev-2", "serialNumber": "BBB", "isTrusted": False}

    resp1 = _make_response(_devices_page([page1_device], has_next_page=True, end_cursor="cursor1"))
    resp2 = _make_response(_devices_page([page2_device], has_next_page=False))

    call_count = 0

    async def _mock_request(*args, **kwargs) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return resp1 if call_count == 1 else resp2

    with patch("src.twingate.client.request_with_retry", new=_mock_request):
        async with TwingateClient("tenant", "key") as client:
            devices = await client.list_untrusted_devices()

    assert len(devices) == 2
    assert call_count == 2
    serials = [d.serial_number for d in devices]
    assert "AAA" in serials
    assert "BBB" in serials


@pytest.mark.asyncio
async def test_list_untrusted_devices_empty() -> None:
    response = _make_response(_devices_page([]))

    with patch("src.twingate.client.request_with_retry", new=AsyncMock(return_value=response)):
        async with TwingateClient("tenant", "key") as client:
            devices = await client.list_untrusted_devices()

    assert devices == []


@pytest.mark.asyncio
async def test_list_untrusted_devices_graphql_error_raises() -> None:
    error_body = {"errors": [{"message": "Unauthorized"}]}
    response = _make_response(error_body)

    with patch("src.twingate.client.request_with_retry", new=AsyncMock(return_value=response)):
        async with TwingateClient("tenant", "key") as client:
            with pytest.raises(ValueError, match="Unauthorized"):
                await client.list_untrusted_devices()


@pytest.mark.asyncio
async def test_list_untrusted_devices_http_error_propagates() -> None:
    with patch(
        "src.twingate.client.request_with_retry",
        new=AsyncMock(side_effect=httpx.RequestError("connection refused")),
    ):
        async with TwingateClient("tenant", "key") as client:
            with pytest.raises(httpx.RequestError):
                await client.list_untrusted_devices()


# ---------------------------------------------------------------------------
# trust_device
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trust_device_success() -> None:
    response = _make_response(_trust_response(ok=True))

    with patch("src.twingate.client.request_with_retry", new=AsyncMock(return_value=response)):
        async with TwingateClient("tenant", "key") as client:
            result = await client.trust_device("dev-1")

    assert result.ok is True
    assert result.entity is not None
    assert result.entity.id == "dev-1"
    assert result.entity.is_trusted is True


@pytest.mark.asyncio
async def test_trust_device_mutation_returns_ok_false() -> None:
    response = _make_response(_trust_response(ok=False, error="Device not found"))

    with patch("src.twingate.client.request_with_retry", new=AsyncMock(return_value=response)):
        async with TwingateClient("tenant", "key") as client:
            result = await client.trust_device("bad-id")

    assert result.ok is False
    assert result.error == "Device not found"


@pytest.mark.asyncio
async def test_trust_device_http_error_propagates() -> None:
    with patch(
        "src.twingate.client.request_with_retry",
        new=AsyncMock(
            side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())
        ),
    ):
        async with TwingateClient("tenant", "key") as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.trust_device("dev-1")


# ---------------------------------------------------------------------------
# Context manager guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_not_used_as_context_manager_raises() -> None:
    client = TwingateClient("tenant", "key")
    with pytest.raises(RuntimeError, match="context manager"):
        await client.list_untrusted_devices()


# ---------------------------------------------------------------------------
# Endpoint construction
# ---------------------------------------------------------------------------


def test_endpoint_built_from_tenant() -> None:
    client = TwingateClient("mycompany", "key")
    assert client._endpoint == "https://mycompany.twingate.com/api/graphql/"


# ---------------------------------------------------------------------------
# Device model field mapping
# ---------------------------------------------------------------------------


def test_device_model_aliases() -> None:
    device = TwingateDevice(
        id="d1",
        name="Alice's Mac",
        serialNumber="SN123",
        osName="macOS",
        osVersion="14.0",
        isTrusted=False,
        activeState="ACTIVE",
    )
    assert device.serial_number == "SN123"
    assert device.os_name == "macOS"
    assert device.is_trusted is False
