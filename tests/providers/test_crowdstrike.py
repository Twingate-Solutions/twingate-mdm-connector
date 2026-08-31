"""Unit tests for src/providers/crowdstrike.py — all HTTP calls are mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import CrowdStrikeComplianceConfig, CrowdStrikeConfig
from src.providers.crowdstrike import (
    _DETAILS_BATCH,
    _DETAILS_PATH,
    _ONLINE_STATE_BATCH,
    _ONLINE_STATE_PATH,
    _SCROLL_PATH,
    _TOKEN_PATH,
    CrowdStrikeProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    cloud: str = "us-1",
    **compliance: bool,
) -> CrowdStrikeConfig:
    return CrowdStrikeConfig(
        type="crowdstrike",
        enabled=True,
        cloud=cloud,  # type: ignore[arg-type]
        client_id="test-client-id",
        client_secret="test-client-secret",
        compliance=CrowdStrikeComplianceConfig(**compliance),
    )


def _make_response(body: object, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _token_response() -> MagicMock:
    return _make_response({"access_token": "falcon-token", "expires_in": 1799})


def _host(
    device_id: str = "aid-1",
    hostname: str = "workstation-01",
    serial: str = "CS-SN-001",
    status: str = "normal",
    rfm: str | None = "no",
    last_seen: str | None = "2024-01-15T10:00:00Z",
) -> dict:
    host: dict = {
        "device_id": device_id,
        "hostname": hostname,
        "serial_number": serial,
        "status": status,
        "platform_name": "Windows",
        "os_version": "Windows 11",
        "provision_status": "Provisioned",
    }
    if rfm is not None:
        host["reduced_functionality_mode"] = rfm
    if last_seen is not None:
        host["last_seen"] = last_seen
    return host


def _scroll_page(host_ids: list[str], offset: str | None = None) -> MagicMock:
    return _make_response(
        {
            "resources": host_ids,
            "meta": {"pagination": {"offset": offset, "limit": 5000, "total": 2}},
        }
    )


class _Router:
    """Dispatch mocked ``request_with_retry`` calls by request path.

    One ``list_devices()`` call issues structurally different requests (scroll,
    details POST, optionally online-state), so ordered ``side_effect`` lists get
    brittle fast.  This router answers by path and records what was asked for.
    """

    def __init__(
        self,
        scroll_pages: list[MagicMock] | None = None,
        hosts: list[dict] | None = None,
        online_states: dict[str, str] | None = None,
    ) -> None:
        self._scroll_pages = scroll_pages or []
        self._hosts_by_id = {h["device_id"]: h for h in (hosts or [])}
        self._online_states = online_states or {}
        self.calls: dict[str, int] = {
            _TOKEN_PATH: 0,
            _SCROLL_PATH: 0,
            _DETAILS_PATH: 0,
            _ONLINE_STATE_PATH: 0,
        }
        self.detail_batches: list[list[str]] = []
        self.online_batches: list[list[str]] = []
        self.scroll_params: list[dict] = []

    async def __call__(self, client, method, url, **kwargs) -> MagicMock:
        self.calls[url] = self.calls.get(url, 0) + 1

        if url == _TOKEN_PATH:
            return _token_response()

        if url == _SCROLL_PATH:
            self.scroll_params.append(kwargs.get("params") or {})
            return self._scroll_pages[self.calls[url] - 1]

        if url == _DETAILS_PATH:
            ids = (kwargs.get("json") or {})["ids"]
            self.detail_batches.append(ids)
            return _make_response(
                {"resources": [self._hosts_by_id[i] for i in ids if i in self._hosts_by_id]}
            )

        if url == _ONLINE_STATE_PATH:
            ids = (kwargs.get("params") or {})["ids"]
            self.online_batches.append(ids)
            return _make_response(
                {
                    "resources": [
                        {"id": i, "state": self._online_states[i]}
                        for i in ids
                        if i in self._online_states
                    ]
                }
            )

        raise AssertionError(f"unexpected request path: {url}")


def _authed(provider: CrowdStrikeProvider) -> CrowdStrikeProvider:
    provider._token_cache.set("falcon-token", 1799)
    return provider


# ---------------------------------------------------------------------------
# name / cloud selection
# ---------------------------------------------------------------------------


def test_name_is_crowdstrike() -> None:
    assert CrowdStrikeProvider(_make_config()).name == "crowdstrike"


def test_default_cloud_is_us1() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider._base_url == "https://api.crowdstrike.com"


def test_eu_cloud_selects_regional_base_url() -> None:
    provider = CrowdStrikeProvider(_make_config(cloud="eu-1"))
    assert provider._base_url == "https://api.eu-1.crowdstrike.com"


def test_unknown_cloud_is_a_config_error() -> None:
    with pytest.raises(Exception):
        CrowdStrikeConfig(
            type="crowdstrike",
            enabled=True,
            cloud="us1",  # type: ignore[arg-type]
            client_id="x",
            client_secret="y",
        )


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_caches_token() -> None:
    provider = CrowdStrikeProvider(_make_config())
    mock = AsyncMock(return_value=_token_response())

    with patch("src.providers.crowdstrike.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_awaited_once()
    assert provider._token_cache.token == "falcon-token"
    # Client-credentials grant is sent as form data, not JSON.
    assert mock.await_args.kwargs["data"]["client_id"] == "test-client-id"
    assert mock.await_args.kwargs["data"]["client_secret"] == "test-client-secret"


@pytest.mark.asyncio
async def test_authenticate_skips_when_token_fresh() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    mock = AsyncMock()

    with patch("src.providers.crowdstrike.request_with_retry", new=mock):
        await provider.authenticate()

    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_raises_on_token_error() -> None:
    provider = CrowdStrikeProvider(_make_config())
    err = _make_response({"errors": [{"message": "access denied"}]}, status_code=403)
    err.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=err
    )

    with patch(
        "src.providers.crowdstrike.request_with_retry", new=AsyncMock(return_value=err)
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.authenticate()


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_raises_if_not_authenticated() -> None:
    provider = CrowdStrikeProvider(_make_config())
    with pytest.raises(RuntimeError, match="authenticate\\(\\) must be called first"):
        await provider.list_devices()


@pytest.mark.asyncio
async def test_list_devices_single_page() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    hosts = [
        _host("aid-1", serial="SN-AAA"),
        _host("aid-2", hostname="laptop-02", serial="SN-BBB"),
    ]
    router = _Router(scroll_pages=[_scroll_page(["aid-1", "aid-2"])], hosts=hosts)

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert {d.serial_number for d in result} == {"SN-AAA", "SN-BBB"}
    assert router.calls[_SCROLL_PATH] == 1
    assert router.calls[_DETAILS_PATH] == 1
    # No live-state lookup unless compliance.require_live is set.
    assert router.calls[_ONLINE_STATE_PATH] == 0
    assert result[0].hostname == "workstation-01"
    assert result[0].os_name == "Windows"
    assert result[0].os_version == "Windows 11"
    assert result[0].raw["device_id"] == "aid-1"


@pytest.mark.asyncio
async def test_list_devices_normalises_serial() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", serial="  abc-def  ")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result[0].serial_number == "ABC-DEF"


@pytest.mark.asyncio
async def test_list_devices_skips_host_with_no_serial() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1", "aid-2"])],
        hosts=[_host("aid-1", serial=""), _host("aid-2", serial="SN-KEEP")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert [d.serial_number for d in result] == ["SN-KEEP"]


@pytest.mark.asyncio
async def test_list_devices_parses_last_seen() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", last_seen="2024-01-15T10:00:00Z")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result[0].last_seen is not None
    assert result[0].last_seen.year == 2024
    assert result[0].last_seen.tzinfo is not None


@pytest.mark.asyncio
async def test_list_devices_tolerates_unparseable_last_seen() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", last_seen="not-a-timestamp")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result[0].last_seen is None
    assert result[0].is_online is False


# ---------------------------------------------------------------------------
# list_devices — scroll pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_devices_follows_scroll_offset() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[
            _scroll_page(["aid-1"], offset="offset-token-1"),
            _scroll_page(["aid-2"], offset=None),
        ],
        hosts=[_host("aid-1", serial="SN-1"), _host("aid-2", serial="SN-2")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert router.calls[_SCROLL_PATH] == 2
    assert len(result) == 2
    # First page must not send an offset; the second must send the returned one.
    assert "offset" not in router.scroll_params[0]
    assert router.scroll_params[1]["offset"] == "offset-token-1"


@pytest.mark.asyncio
async def test_scroll_stops_on_empty_page_despite_offset_token() -> None:
    """The last scroll page can return an offset token with zero resources."""
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[
            _scroll_page(["aid-1"], offset="offset-token-1"),
            _scroll_page([], offset="offset-token-2"),
        ],
        hosts=[_host("aid-1", serial="SN-1")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert router.calls[_SCROLL_PATH] == 2
    assert len(result) == 1


@pytest.mark.asyncio
async def test_empty_inventory_makes_no_details_call() -> None:
    """A POST with an empty ids list is a 400 — it must never be sent."""
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(scroll_pages=[_scroll_page([], offset=None)])

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result == []
    assert router.calls[_DETAILS_PATH] == 0


@pytest.mark.asyncio
async def test_details_are_batched() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    host_ids = [f"aid-{i}" for i in range(_DETAILS_BATCH + 1)]
    hosts = [_host(hid, serial=f"SN-{hid}") for hid in host_ids]
    router = _Router(scroll_pages=[_scroll_page(host_ids)], hosts=hosts)

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert router.calls[_DETAILS_PATH] == 2
    assert len(router.detail_batches[0]) == _DETAILS_BATCH
    assert len(router.detail_batches[1]) == 1
    assert len(result) == _DETAILS_BATCH + 1


# ---------------------------------------------------------------------------
# is_online
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_online_true_when_sensor_has_checked_in() -> None:
    """Default behaviour: a powered-off but enrolled host is not rejected."""
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", last_seen="2024-01-15T10:00:00Z")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result[0].is_online is True


@pytest.mark.asyncio
async def test_is_online_false_when_host_never_checked_in() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config()))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", last_seen=None)],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result[0].is_online is False


@pytest.mark.asyncio
async def test_require_live_uses_online_state_endpoint() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config(require_live=True)))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1", "aid-2", "aid-3"])],
        hosts=[
            _host("aid-1", serial="SN-1"),
            _host("aid-2", serial="SN-2"),
            _host("aid-3", serial="SN-3"),
        ],
        online_states={"aid-1": "online", "aid-2": "offline"},
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert router.calls[_ONLINE_STATE_PATH] == 1
    by_serial = {d.serial_number: d for d in result}
    assert by_serial["SN-1"].is_online is True
    assert by_serial["SN-2"].is_online is False
    # aid-3 absent from the response — treated as not live.
    assert by_serial["SN-3"].is_online is False


@pytest.mark.asyncio
async def test_require_live_batches_online_state_lookups() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config(require_live=True)))
    host_ids = [f"aid-{i}" for i in range(_ONLINE_STATE_BATCH + 5)]
    router = _Router(
        scroll_pages=[_scroll_page(host_ids)],
        hosts=[_host(hid, serial=f"SN-{hid}") for hid in host_ids],
        online_states={hid: "online" for hid in host_ids},
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert router.calls[_ONLINE_STATE_PATH] == 2
    assert len(router.online_batches[0]) == _ONLINE_STATE_BATCH
    assert len(router.online_batches[1]) == 5
    assert all(d.is_online for d in result)


@pytest.mark.asyncio
async def test_require_live_skips_online_state_when_no_hosts_have_serials() -> None:
    provider = _authed(CrowdStrikeProvider(_make_config(require_live=True)))
    router = _Router(
        scroll_pages=[_scroll_page(["aid-1"])],
        hosts=[_host("aid-1", serial="")],
    )

    with patch("src.providers.crowdstrike.request_with_retry", new=router):
        result = await provider.list_devices()

    assert result == []
    assert router.calls[_ONLINE_STATE_PATH] == 0


# ---------------------------------------------------------------------------
# determine_compliance
# ---------------------------------------------------------------------------


def test_compliance_normal_host_passes() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(status="normal", rfm="no")) is True


def test_compliance_lifted_containment_passes() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(status="lifted_containment")) is True


def test_compliance_contained_host_fails() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(status="contained")) is False


def test_compliance_containment_pending_host_fails() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(status="containment_pending")) is False


def test_compliance_status_comparison_is_case_insensitive() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(status="Contained")) is False


def test_compliance_rfm_host_fails() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(rfm="yes")) is False


def test_compliance_fails_open_on_missing_rfm() -> None:
    """reduced_functionality_mode is commonly absent on macOS / Linux hosts."""
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(rfm=None)) is True


def test_compliance_fails_open_on_unknown_rfm() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider.determine_compliance(_host(rfm="unknown")) is True


def test_compliance_fails_open_on_missing_status() -> None:
    provider = CrowdStrikeProvider(_make_config())
    host = _host()
    del host["status"]
    assert provider.determine_compliance(host) is True


def test_compliance_containment_check_can_be_disabled() -> None:
    provider = CrowdStrikeProvider(_make_config(require_not_contained=False))
    assert provider.determine_compliance(_host(status="contained")) is True


def test_compliance_sensor_check_can_be_disabled() -> None:
    provider = CrowdStrikeProvider(_make_config(require_full_sensor=False))
    assert provider.determine_compliance(_host(rfm="yes")) is True


def test_compliance_is_reflected_on_the_built_device() -> None:
    provider = CrowdStrikeProvider(_make_config())
    assert provider._build_device(_host(status="contained"), {}).is_compliant is False
    assert provider._build_device(_host(status="normal"), {}).is_compliant is True
