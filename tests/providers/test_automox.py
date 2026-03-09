"""Unit tests for src/providers/automox.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import AutomoxConfig
from src.providers.automox import AutomoxProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> AutomoxConfig:
    return AutomoxConfig(
        type="automox",
        enabled=True,
        org_id="org-12345",
        api_key="test-api-key",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _server(
    server_id: int = 1,
    serial: str = "AX-SN-001",
    agent_status: str = "connected",
    is_compatible: bool = True,
    pending_patches: int = 0,
    os_family: str = "windows",
) -> dict:
    return {
        "id": server_id,
        "name": f"server-{server_id}",
        "serial_number": serial,
        "os_family": os_family,
        "os_name": "Windows 10",
        "os_version": "10.0.19041",
        "is_compatible": is_compatible,
        "pending_patches": pending_patches,
        "needs_reboot": False,
        "status": {"agent_status": agent_status},
        "last_disconnect_time": "2024-01-10T12:00:00Z",
    }


# ---------------------------------------------------------------------------
# authenticate — no-op for Automox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_is_noop() -> None:
    """authenticate() must complete without making any HTTP calls."""
    provider = AutomoxProvider(_make_config())
    mock = AsyncMock()
    with patch("src.providers.automox.request_with_retry", new=mock):
        await provider.authenticate()
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_devices — single page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = AutomoxProvider(_make_config())
    resp = _make_response([_server(1, "SN-AAA"), _server(2, "SN-BBB")])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    serials = {d.serial_number for d in result}
    assert "SN-AAA" in serials
    assert "SN-BBB" in serials


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = AutomoxProvider(_make_config())
    resp = _make_response([_server(1, "  ax-sn-001  ")])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "AX-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = AutomoxProvider(_make_config())
    no_serial = {"id": 99, "name": "ghost", "status": {}}
    resp = _make_response([no_serial])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_empty_response() -> None:
    provider = AutomoxProvider(_make_config())
    resp = _make_response([])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_paginates_until_partial_page() -> None:
    """Two full pages followed by a partial page → stops after third call."""
    provider = AutomoxProvider(_make_config())

    from src.providers.automox import _PAGE_LIMIT

    page1 = [_server(i, f"SN-{i}") for i in range(_PAGE_LIMIT)]
    page2 = [_server(i + _PAGE_LIMIT, f"SN-{i + _PAGE_LIMIT}") for i in range(_PAGE_LIMIT)]
    page3 = [_server(9999, "SN-LAST")]

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(page1)
        if call_count == 2:
            return _make_response(page2)
        return _make_response(page3)

    with patch("src.providers.automox.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 3
    assert len(result) == _PAGE_LIMIT * 2 + 1


@pytest.mark.asyncio
async def test_list_devices_passes_correct_page_params() -> None:
    """Verify that page=0 and page=1 are passed on successive calls."""
    provider = AutomoxProvider(_make_config())

    from src.providers.automox import _PAGE_LIMIT

    page1 = [_server(i, f"SN-{i}") for i in range(_PAGE_LIMIT)]
    page2 = [_server(9999, "SN-LAST")]

    captured_params: list[dict] = []
    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        captured_params.append(kwargs.get("params", {}))
        return _make_response(page1) if call_count == 1 else _make_response(page2)

    with patch("src.providers.automox.request_with_retry", new=_mock):
        await provider.list_devices()

    assert captured_params[0]["page"] == 0
    assert captured_params[1]["page"] == 1


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_compatible_no_patches() -> None:
    provider = AutomoxProvider(_make_config())
    assert provider.determine_compliance(_server(is_compatible=True, pending_patches=0)) is True


def test_compliance_not_compatible() -> None:
    provider = AutomoxProvider(_make_config())
    assert provider.determine_compliance(_server(is_compatible=False, pending_patches=0)) is False


def test_compliance_pending_patches() -> None:
    provider = AutomoxProvider(_make_config())
    assert provider.determine_compliance(_server(is_compatible=True, pending_patches=5)) is False


def test_compliance_missing_fields_defaults_to_non_compliant() -> None:
    """A bare server dict without is_compatible defaults to False."""
    provider = AutomoxProvider(_make_config())
    assert provider.determine_compliance({}) is False


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connected_agent_is_online() -> None:
    provider = AutomoxProvider(_make_config())
    resp = _make_response([_server(1, "SN-X", agent_status="connected")])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_disconnected_agent_is_not_online() -> None:
    provider = AutomoxProvider(_make_config())
    resp = _make_response([_server(1, "SN-Y", agent_status="disconnected")])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].is_online is False


@pytest.mark.asyncio
async def test_missing_status_is_not_online() -> None:
    provider = AutomoxProvider(_make_config())
    raw = {"id": 1, "serial_number": "SN-Z"}
    resp = _make_response([raw])

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_raises_on_http_error() -> None:
    provider = AutomoxProvider(_make_config())
    err_resp = _make_response({}, status_code=403)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.automox.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.list_devices()


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert AutomoxProvider(_make_config()).name == "automox"
