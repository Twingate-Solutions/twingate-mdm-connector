"""Unit tests for src/providers/rippling.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import RipplingConfig
from src.providers.rippling import RipplingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> RipplingConfig:
    return RipplingConfig(
        type="rippling",
        enabled=True,
        client_id="rippling-client-id",
        client_secret="rippling-client-secret",
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _token_response() -> MagicMock:
    return _make_response({"access_token": "rippling-token", "expires_in": 3600})


def _device(
    device_id: str = "dev-001",
    serial: str = "RPLNG-SN-001",
    management_status: str = "ACTIVE",
    os_type: str = "macOS",
    os_version: str = "14.0",
    last_seen: str = "2024-01-15T10:00:00Z",
) -> dict:
    return {
        "id": device_id,
        "name": f"device-{device_id}",
        "serialNumber": serial,
        "managementStatus": management_status,
        "osType": os_type,
        "osVersion": os_version,
        "lastSeen": last_seen,
    }


def _paginated_response(
    results: list[dict],
    next_url: str | None = None,
) -> MagicMock:
    return _make_response({"results": results, "next": next_url})


def _list_response(results: list[dict]) -> MagicMock:
    """Simulate an API that returns a plain list instead of an envelope."""
    return _make_response(results)


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_fetches_token() -> None:
    provider = RipplingProvider(_make_config())
    token_resp = _token_response()

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=token_resp)):
        await provider.authenticate()

    assert provider._token_cache.token == "rippling-token"
    assert not provider._token_cache.needs_refresh()


@pytest.mark.asyncio
async def test_authenticate_skips_when_token_fresh() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("existing-token", 3600)

    mock = AsyncMock()
    with patch("src.providers.rippling.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_raises_on_http_error() -> None:
    provider = RipplingProvider(_make_config())
    err_resp = _make_response({}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices — paginated envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_single_page_envelope() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    resp = _paginated_response([_device("d1", "SN-AAA"), _device("d2", "SN-BBB")])

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-AAA", "SN-BBB"}


@pytest.mark.asyncio
async def test_list_devices_follows_next_url() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    page1 = _paginated_response(
        [_device("d1", "SN-1")],
        next_url="https://api.rippling.com/platform/api/devices?page=2",
    )
    page2 = _paginated_response([_device("d2", "SN-2")])

    call_count = 0

    async def _mock(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return page1 if call_count == 1 else page2

    with patch("src.providers.rippling.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert call_count == 2
    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-1", "SN-2"}


@pytest.mark.asyncio
async def test_list_devices_plain_list_response() -> None:
    """API returns a plain list instead of a paginated envelope."""
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    resp = _list_response([_device("d1", "SN-LIST-1"), _device("d2", "SN-LIST-2")])

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-LIST-1", "SN-LIST-2"}


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    resp = _paginated_response([_device(serial="  rippling-sn-001  ")])

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "RIPPLING-SN-001"


@pytest.mark.asyncio
async def test_list_devices_skips_missing_serial() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    no_serial = {"id": "ghost", "name": "ghost-device", "managementStatus": "ACTIVE"}
    resp = _paginated_response([no_serial])

    with patch("src.providers.rippling.request_with_retry", new=AsyncMock(return_value=resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_list_devices_empty_response() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    with patch(
        "src.providers.rippling.request_with_retry",
        new=AsyncMock(return_value=_paginated_response([])),
    ):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_active_status() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance(_device(management_status="ACTIVE")) is True


def test_compliance_managed_status() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance(_device(management_status="MANAGED")) is True


def test_compliance_active_case_insensitive() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance(_device(management_status="active")) is True


def test_compliance_pending_is_non_compliant() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance(_device(management_status="PENDING")) is False


def test_compliance_inactive_is_non_compliant() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance(_device(management_status="INACTIVE")) is False


def test_compliance_missing_status_is_non_compliant() -> None:
    provider = RipplingProvider(_make_config())
    assert provider.determine_compliance({"serialNumber": "SN-001"}) is False


# ---------------------------------------------------------------------------
# online status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_online_when_active() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    with patch(
        "src.providers.rippling.request_with_retry",
        new=AsyncMock(return_value=_paginated_response([_device(management_status="ACTIVE")])),
    ):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_offline_when_inactive() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    with patch(
        "src.providers.rippling.request_with_retry",
        new=AsyncMock(return_value=_paginated_response([_device(management_status="INACTIVE")])),
    ):
        result = await provider.list_devices()

    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# last_seen parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_seen_parsed() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    with patch(
        "src.providers.rippling.request_with_retry",
        new=AsyncMock(
            return_value=_paginated_response([_device(last_seen="2024-03-01T12:00:00Z")])
        ),
    ):
        result = await provider.list_devices()

    assert result[0].last_seen is not None
    assert result[0].last_seen.year == 2024
    assert result[0].last_seen.month == 3


@pytest.mark.asyncio
async def test_last_seen_none_when_absent() -> None:
    provider = RipplingProvider(_make_config())
    provider._token_cache.set("rippling-token", 3600)

    raw = {
        "id": "d1",
        "serialNumber": "SN-001",
        "managementStatus": "ACTIVE",
    }

    with patch(
        "src.providers.rippling.request_with_retry",
        new=AsyncMock(return_value=_paginated_response([raw])),
    ):
        result = await provider.list_devices()

    assert result[0].last_seen is None


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert RipplingProvider(_make_config()).name == "rippling"
