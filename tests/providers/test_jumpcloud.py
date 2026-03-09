"""Unit tests for src/providers/jumpcloud.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import JumpCloudConfig
from src.providers.jumpcloud import JumpCloudProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> JumpCloudConfig:
    return JumpCloudConfig(
        type="jumpcloud",
        enabled=True,
        api_key="jc-test-api-key",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _system(
    system_id: str = "sys-1",
    serial: str = "JC-SN-001",
    active: bool = True,
    fde_active: bool | None = None,
    os: str = "Mac OS X",
    os_version: str = "14.0",
) -> dict:
    result: dict = {
        "_id": system_id,
        "displayName": f"Device-{system_id}",
        "hostname": f"host-{system_id}.local",
        "serialNumber": serial,
        "os": os,
        "osVersion": os_version,
        "active": active,
        "lastContact": "2024-01-15T10:00:00Z",
        "agentVersion": "1.5.0",
    }
    if fde_active is not None:
        result["fde"] = {"active": fde_active}
    return result


def _page(systems: list[dict], total_count: int | None = None) -> MagicMock:
    count = total_count if total_count is not None else len(systems)
    return _make_response({"results": systems, "totalCount": count})


# ---------------------------------------------------------------------------
# authenticate — no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_is_noop() -> None:
    provider = JumpCloudProvider(_make_config())
    mock = AsyncMock()
    with patch("src.providers.jumpcloud.request_with_retry", new=mock):
        await provider.authenticate()
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_devices — single page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = JumpCloudProvider(_make_config())
    resp = _page([_system("s1", "SN-AAA"), _system("s2", "SN-BBB")])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-AAA", "SN-BBB"}


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = JumpCloudProvider(_make_config())
    resp = _page([_system(serial="  jc-sn-001  ")])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "JC-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = JumpCloudProvider(_make_config())
    no_serial = {"_id": "ghost", "active": True}
    resp = _page([no_serial])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_empty_response() -> None:
    provider = JumpCloudProvider(_make_config())
    resp = _page([])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# list_devices — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_paginates_via_total_count() -> None:
    """Stop when cumulative results reach totalCount."""
    provider = JumpCloudProvider(_make_config())

    from src.providers.jumpcloud import _PAGE_LIMIT

    page1_systems = [_system(f"s{i}", f"SN-{i}") for i in range(_PAGE_LIMIT)]
    page2_systems = [_system("s-last", "SN-LAST")]

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _page(page1_systems, total_count=_PAGE_LIMIT + 1)
        return _page(page2_systems, total_count=_PAGE_LIMIT + 1)

    with patch("src.providers.jumpcloud.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == _PAGE_LIMIT + 1


@pytest.mark.asyncio
async def test_list_devices_stops_on_partial_page() -> None:
    """Stop when a page returns fewer results than the limit."""
    provider = JumpCloudProvider(_make_config())

    from src.providers.jumpcloud import _PAGE_LIMIT

    page1 = [_system(f"s{i}", f"SN-{i}") for i in range(_PAGE_LIMIT)]
    page2 = [_system("s-last", "SN-LAST")]  # partial page

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Set totalCount very high so only partial-page check triggers stop
        if call_count == 1:
            return _page(page1, total_count=9999)
        return _page(page2, total_count=9999)

    with patch("src.providers.jumpcloud.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == _PAGE_LIMIT + 1


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_active_no_fde() -> None:
    provider = JumpCloudProvider(_make_config())
    assert provider.determine_compliance(_system(active=True)) is True


def test_compliance_inactive_agent() -> None:
    provider = JumpCloudProvider(_make_config())
    assert provider.determine_compliance(_system(active=False)) is False


def test_compliance_fde_active() -> None:
    provider = JumpCloudProvider(_make_config())
    assert provider.determine_compliance(_system(active=True, fde_active=True)) is True


def test_compliance_fde_inactive() -> None:
    """FDE explicitly off → non-compliant."""
    provider = JumpCloudProvider(_make_config())
    assert provider.determine_compliance(_system(active=True, fde_active=False)) is False


def test_compliance_missing_fields_defaults_non_compliant() -> None:
    """No active field → defaults to False."""
    provider = JumpCloudProvider(_make_config())
    assert provider.determine_compliance({}) is False


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_system_is_online() -> None:
    provider = JumpCloudProvider(_make_config())
    resp = _page([_system(active=True)])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_inactive_system_is_not_online() -> None:
    provider = JumpCloudProvider(_make_config())
    resp = _page([_system(active=False)])

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_raises_on_http_error() -> None:
    provider = JumpCloudProvider(_make_config())
    err_resp = _make_response({}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.jumpcloud.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.list_devices()


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert JumpCloudProvider(_make_config()).name == "jumpcloud"
