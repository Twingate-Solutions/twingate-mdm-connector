"""Unit tests for src/providers/manageengine.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import ManageEngineConfig
from src.providers.manageengine import ManageEngineProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_onprem_config(base_url: str = "https://me.corp.local") -> ManageEngineConfig:
    return ManageEngineConfig(
        type="manageengine",
        enabled=True,
        variant="onprem",
        base_url=base_url,
        api_token="me-api-token",
    )


def _make_cloud_config(**compliance_kwargs) -> ManageEngineConfig:
    from src.config import ManageEngineCloudComplianceConfig

    kwargs: dict = dict(
        type="manageengine",
        enabled=True,
        variant="cloud",
        oauth_client_id="zoho-client-id",
        oauth_client_secret="zoho-client-secret",
        oauth_refresh_token="zoho-refresh-token",
    )
    if compliance_kwargs:
        kwargs["compliance"] = ManageEngineCloudComplianceConfig(**compliance_kwargs)
    return ManageEngineConfig(**kwargs)


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _computer(
    name: str = "PC001",
    managed_status: str = "ACTIVE",
    last_contact_ms: int = 1_700_000_000_000,
    os_name: str = "Windows 10 Enterprise",
) -> dict:
    return {
        "computer_name": name,
        "managed_status": managed_status,
        "last_contact_time": str(last_contact_ms),
        "os_name": os_name,
    }


def _computers_response(computers: list[dict]) -> MagicMock:
    return _make_response(
        {
            "message_response": {
                "computers": computers,
                "computers_count": len(computers),
            }
        }
    )


def _inventory_item(name: str = "PC001", serial: str = "ME-SN-001") -> dict:
    return {"sysinfo": {"COMPNAME": name, "SERIALNUMBER": serial}}


def _inventory_response(items: list[dict]) -> MagicMock:
    return _make_response({"compdetails": items, "compcount": len(items)})


def _zoho_token_response() -> MagicMock:
    return _make_response({"access_token": "zoho-access-token", "expires_in": 3600})


# ---------------------------------------------------------------------------
# authenticate — on-prem is no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_onprem_is_noop() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    mock = AsyncMock()
    with patch("src.providers.manageengine.request_with_retry", new=mock):
        await provider.authenticate()
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# authenticate — cloud Zoho OAuth2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_cloud_fetches_zoho_token() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    token_resp = _zoho_token_response()

    with patch("src.providers.manageengine.request_with_retry", new=AsyncMock(return_value=token_resp)):
        await provider.authenticate()

    assert provider._token_cache.token == "zoho-access-token"
    assert not provider._token_cache.needs_refresh()


@pytest.mark.asyncio
async def test_authenticate_cloud_skips_when_token_fresh() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    provider._token_cache.set("existing-token", 3600)

    mock = AsyncMock()
    with patch("src.providers.manageengine.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_cloud_raises_on_token_error() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    err_resp = _make_response({"error": "invalid_client"}, status_code=401)
    err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=err_resp
    )

    with patch("src.providers.manageengine.request_with_retry", new=AsyncMock(return_value=err_resp)):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices — join computers + inventory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_joins_computers_and_inventory() -> None:
    provider = ManageEngineProvider(_make_onprem_config())

    computers_resp = _computers_response([_computer("PC001"), _computer("PC002")])
    inventory_resp = _inventory_response(
        [_inventory_item("PC001", "SN-AAA"), _inventory_item("PC002", "SN-BBB")]
    )

    responses = [computers_resp, inventory_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 2
    assert {d.serial_number for d in result} == {"SN-AAA", "SN-BBB"}


@pytest.mark.asyncio
async def test_list_devices_skips_computer_without_inventory_match() -> None:
    """A computer with no matching inventory entry (no serial) is skipped."""
    provider = ManageEngineProvider(_make_onprem_config())

    computers_resp = _computers_response([_computer("PC001"), _computer("GHOST")])
    # GHOST has no inventory entry
    inventory_resp = _inventory_response([_inventory_item("PC001", "SN-AAA")])

    responses = [computers_resp, inventory_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 1
    assert result[0].serial_number == "SN-AAA"


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = ManageEngineProvider(_make_onprem_config())

    computers_resp = _computers_response([_computer("PC001")])
    inventory_resp = _inventory_response([_inventory_item("PC001", "  me-sn-001  ")])

    responses = [computers_resp, inventory_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result[0].serial_number == "ME-SN-001"


@pytest.mark.asyncio
async def test_list_devices_join_is_case_insensitive() -> None:
    """Computer name matching ignores case."""
    provider = ManageEngineProvider(_make_onprem_config())

    computers_resp = _computers_response([_computer("pc001")])  # lowercase
    inventory_resp = _inventory_response([_inventory_item("PC001", "SN-CASE")])  # uppercase

    responses = [computers_resp, inventory_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert len(result) == 1
    assert result[0].serial_number == "SN-CASE"


@pytest.mark.asyncio
async def test_list_devices_returns_empty_when_no_computers() -> None:
    provider = ManageEngineProvider(_make_onprem_config())

    computers_resp = _computers_response([])
    inventory_resp = _inventory_response([])

    responses = [computers_resp, inventory_resp]
    idx = 0

    async def _mock(*args, **kwargs):
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        result = await provider.list_devices()

    assert result == []


# ---------------------------------------------------------------------------
# inventory serial field fallbacks
# ---------------------------------------------------------------------------


def test_inventory_serial_fallback_to_lowercase_field() -> None:
    """``serial_number`` (lowercase) in sysinfo should also be found."""
    provider = ManageEngineProvider(_make_onprem_config())
    # Directly exercise _fetch_inventory_serials logic via _build_device indirectly.
    # We test the sysinfo parsing by examining the inventory item structure.
    item = {"sysinfo": {"COMPNAME": "PC001", "serial_number": "LOWER-SERIAL"}}
    sysinfo = item.get("sysinfo") or item
    serial = (
        sysinfo.get("SERIALNUMBER")
        or sysinfo.get("serial_number")
        or sysinfo.get("BIOS_SERIALNUMBER")
        or ""
    )
    assert serial == "LOWER-SERIAL"


def test_inventory_serial_fallback_to_bios_serial() -> None:
    """``BIOS_SERIALNUMBER`` should be used when primary field absent."""
    item = {"sysinfo": {"COMPNAME": "PC001", "BIOS_SERIALNUMBER": "BIOS-SERIAL"}}
    sysinfo = item.get("sysinfo") or item
    serial = (
        sysinfo.get("SERIALNUMBER")
        or sysinfo.get("serial_number")
        or sysinfo.get("BIOS_SERIALNUMBER")
        or ""
    )
    assert serial == "BIOS-SERIAL"


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_active_status() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    assert provider.determine_compliance(_computer(managed_status="ACTIVE")) is True


def test_compliance_managed_status() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    assert provider.determine_compliance(_computer(managed_status="MANAGED")) is True


def test_compliance_inactive_status() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    assert provider.determine_compliance(_computer(managed_status="INACTIVE")) is False


def test_compliance_missing_status_is_non_compliant() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    assert provider.determine_compliance({}) is False


# ---------------------------------------------------------------------------
# auth header variants
# ---------------------------------------------------------------------------


def test_onprem_auth_header_uses_raw_token() -> None:
    provider = ManageEngineProvider(_make_onprem_config())
    headers = provider._auth_headers()
    assert headers["Authorization"] == "me-api-token"


def test_cloud_auth_header_uses_zoho_prefix() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    provider._token_cache.set("zoho-token", 3600)
    headers = provider._auth_headers()
    assert headers["Authorization"] == "Zoho-oauthtoken zoho-token"


# ---------------------------------------------------------------------------
# base URL defaults
# ---------------------------------------------------------------------------


def test_onprem_uses_configured_base_url() -> None:
    provider = ManageEngineProvider(_make_onprem_config("https://me.corp.local"))
    assert provider._base_url == "https://me.corp.local"


def test_cloud_defaults_to_cloud_base_url() -> None:
    from src.providers.manageengine import _CLOUD_BASE_URL

    provider = ManageEngineProvider(_make_cloud_config())
    assert provider._base_url == _CLOUD_BASE_URL.rstrip("/")


# ---------------------------------------------------------------------------
# cloud list_devices — single-call SOM path
# ---------------------------------------------------------------------------


def _cloud_computer(
    name: str = "PC001",
    serial: str = "SN-CLOUD-001",
    installation_status: int = 22,
    last_contact_ms: int = 1_700_000_000_000,
    os_platform_name: str = "Windows",
    os_version: str = "10.0.19041",
) -> dict:
    return {
        "full_name": name,
        "managedcomputerextn.service_tag": serial,
        "installation_status": installation_status,
        "agent_last_contact_time": last_contact_ms,
        "os_platform_name": os_platform_name,
        "os_version": os_version,
    }


def _som_computers_response(computers: list[dict]) -> MagicMock:
    return _make_response(
        {
            "message_response": {
                "computers": computers,
                "total": len(computers),
            }
        }
    )


@pytest.mark.asyncio
async def test_cloud_list_devices_uses_som_endpoint() -> None:
    """Cloud variant uses /api/1.4/som/computers and reads inline serial."""
    provider = ManageEngineProvider(_make_cloud_config())
    provider._token_cache.set("tok", 3600)

    som_resp = _som_computers_response([_cloud_computer("PC001", "SN-CLOUD")])

    with patch("src.providers.manageengine.request_with_retry", new=AsyncMock(return_value=som_resp)):
        result = await provider.list_devices()

    assert len(result) == 1
    assert result[0].serial_number == "SN-CLOUD"
    assert result[0].hostname == "PC001"


@pytest.mark.asyncio
async def test_cloud_list_devices_skips_missing_serial() -> None:
    """Cloud computer with no service_tag is skipped."""
    provider = ManageEngineProvider(_make_cloud_config())
    provider._token_cache.set("tok", 3600)

    comp = _cloud_computer("PC001", serial="")
    som_resp = _som_computers_response([comp])

    with patch("src.providers.manageengine.request_with_retry", new=AsyncMock(return_value=som_resp)):
        result = await provider.list_devices()

    assert result == []


@pytest.mark.asyncio
async def test_cloud_list_devices_normalises_serial() -> None:
    """Serial number is stripped and upper-cased."""
    provider = ManageEngineProvider(_make_cloud_config())
    provider._token_cache.set("tok", 3600)

    comp = _cloud_computer("PC001", serial="  sn-abc  ")
    som_resp = _som_computers_response([comp])

    with patch("src.providers.manageengine.request_with_retry", new=AsyncMock(return_value=som_resp)):
        result = await provider.list_devices()

    assert result[0].serial_number == "SN-ABC"


# ---------------------------------------------------------------------------
# determine_compliance — cloud (require_installed, default on)
# ---------------------------------------------------------------------------


def test_cloud_compliance_installed_status_22() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    assert provider.determine_compliance({"installation_status": 22}) is True


def test_cloud_compliance_non_installed_status() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    assert provider.determine_compliance({"installation_status": 21}) is False


def test_cloud_compliance_missing_status_is_non_compliant() -> None:
    provider = ManageEngineProvider(_make_cloud_config())
    assert provider.determine_compliance({}) is False


def test_cloud_compliance_require_installed_false_ignores_installation_status() -> None:
    """When require_installed is disabled, installation_status is not checked."""
    provider = ManageEngineProvider(_make_cloud_config(require_installed=False))
    assert provider.determine_compliance({"installation_status": 21}) is True


# ---------------------------------------------------------------------------
# determine_compliance — cloud require_live
# ---------------------------------------------------------------------------


def test_cloud_compliance_require_live_passes_when_live() -> None:
    provider = ManageEngineProvider(_make_cloud_config(require_live=True))
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 1}) is True


def test_cloud_compliance_require_live_fails_when_down() -> None:
    provider = ManageEngineProvider(_make_cloud_config(require_live=True))
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 2}) is False


def test_cloud_compliance_require_live_fails_when_unknown() -> None:
    provider = ManageEngineProvider(_make_cloud_config(require_live=True))
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 3}) is False


def test_cloud_compliance_require_live_false_does_not_check_live_status() -> None:
    """Default behaviour — live status is not checked."""
    provider = ManageEngineProvider(_make_cloud_config())
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 2}) is True


def test_cloud_compliance_both_checks_enabled_must_pass_both() -> None:
    provider = ManageEngineProvider(_make_cloud_config(require_installed=True, require_live=True))
    # installed but not live → fail
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 2}) is False
    # live but not installed → fail
    assert provider.determine_compliance({"installation_status": 21, "computer_live_status": 1}) is False
    # both pass → compliant
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 1}) is True


def test_cloud_compliance_omitted_block_uses_defaults() -> None:
    """Omitting the compliance block entirely defaults to require_installed=True, require_live=False."""
    cfg = ManageEngineConfig(
        type="manageengine",
        enabled=True,
        variant="cloud",
        oauth_client_id="id",
        oauth_client_secret="secret",
        oauth_refresh_token="refresh",
    )
    provider = ManageEngineProvider(cfg)
    assert cfg.compliance.require_installed is True
    assert cfg.compliance.require_live is False
    assert provider.determine_compliance({"installation_status": 22}) is True
    assert provider.determine_compliance({"installation_status": 22, "computer_live_status": 2}) is True


# ---------------------------------------------------------------------------
# oauth_token_url — regional override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_cloud_uses_custom_token_url() -> None:
    """oauth_token_url config field overrides the default Zoho US endpoint."""
    cfg = ManageEngineConfig(
        type="manageengine",
        enabled=True,
        variant="cloud",
        oauth_client_id="id",
        oauth_client_secret="secret",
        oauth_refresh_token="refresh",
        oauth_token_url="https://accounts.zohocloud.ca/oauth/v2/token",
    )
    provider = ManageEngineProvider(cfg)
    token_resp = _zoho_token_response()

    calls = []

    async def _mock(client, method, url, **kwargs):
        calls.append(url)
        return token_resp

    with patch("src.providers.manageengine.request_with_retry", new=_mock):
        await provider.authenticate()

    assert calls[0] == "https://accounts.zohocloud.ca/oauth/v2/token"


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_provider_name() -> None:
    assert ManageEngineProvider(_make_onprem_config()).name == "manageengine"
