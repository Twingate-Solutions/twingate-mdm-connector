"""CrowdStrike Falcon provider plugin.

Auth:   OAuth2 Client Credentials — client_id + client_secret (30-minute token)
Token:  POST https://{cloud}/oauth2/token
API:    GET  /devices/queries/devices-scroll/v1  → host IDs (scroll pagination)
        POST /devices/entities/devices/v2        → host details (≤ 5000 IDs/request)
        GET  /devices/entities/online-state/v1   → live sensor state (≤ 100 IDs/request,
                                                   only when compliance.require_live)
Scope:  Hosts: READ
Docs:   https://developer.crowdstrike.com/api-reference/collections/hosts/
"""

from collections.abc import Iterator
from datetime import datetime

import structlog

from src.config import CrowdStrikeConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

# Falcon cloud (region) API base URLs.
_CLOUD_BASES: dict[str, str] = {
    "us-1": "https://api.crowdstrike.com",
    "us-2": "https://api.us-2.crowdstrike.com",
    "us-3": "https://api.us-3.crowdstrike.com",
    "eu-1": "https://api.eu-1.crowdstrike.com",
    "us-gov-1": "https://api.laggar.gcw.crowdstrike.com",
    "us-gov-2": "https://api.us-gov-2.crowdstrike.mil",
}

_TOKEN_PATH = "/oauth2/token"
_SCROLL_PATH = "/devices/queries/devices-scroll/v1"
_DETAILS_PATH = "/devices/entities/devices/v2"
_ONLINE_STATE_PATH = "/devices/entities/online-state/v1"

# Scroll page size — the API accepts 1-10000; 5000 keeps responses modest.
_SCROLL_LIMIT = 5000
# Host-detail batch size — the POST body accepts up to 5000 IDs; 500 keeps each
# response small enough to parse without a memory spike on large fleets.
_DETAILS_BATCH = 500
# online-state/v1 documents a maximum of 100 IDs per request.
_ONLINE_STATE_BATCH = 100
_MAX_PAGES = 500

# Falcon token lifetime is 30 minutes; used only if the API omits expires_in.
_DEFAULT_TOKEN_TTL = 1799

# ``status`` values meaning an analyst has network-contained (isolated) the host.
_CONTAINED_STATUSES = frozenset({"contained", "containment_pending"})


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield successive *size*-length slices of *items*."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


class CrowdStrikeProvider(ProviderPlugin):
    """CrowdStrike Falcon provider plugin.

    Fetches the full host inventory via the Falcon Hosts API in two sequential
    phases:

    1. **Enumerate** — drain ``/devices/queries/devices-scroll/v1`` into a flat
       list of host IDs (AIDs).  The scroll offset pointer expires after two
       minutes of inactivity, so this phase runs back-to-back with no detail
       calls interleaved.
    2. **Hydrate** — POST batches of those IDs to
       ``/devices/entities/devices/v2`` for the full host records.

    Compliance is derived from network-containment status and sensor
    Reduced Functionality Mode (RFM), both individually configurable.  Absent
    fields fail *open*: ``reduced_functionality_mode`` is commonly missing on
    macOS and Linux hosts, and a host that simply does not report a signal is
    not treated as failing it.

    ``is_online`` defaults to "this sensor has checked in at least once", which
    keeps hosts that are merely powered off from being rejected outright by
    ``trust.require_online``.  Set ``compliance.require_live: true`` to instead
    query ``/devices/entities/online-state/v1`` and require a live sensor.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "crowdstrike"

    def __init__(self, config: CrowdStrikeConfig) -> None:
        """Initialise the CrowdStrike provider.

        Args:
            config: CrowdStrike configuration from YAML / env.
        """
        self._config = config
        self._token_cache = TokenCache()
        self._base_url = _CLOUD_BASES[config.cloud]
        self._client = build_client(base_url=self._base_url)

    async def authenticate(self) -> None:
        """Obtain or refresh the OAuth2 access token.

        Uses the client credentials flow against ``/oauth2/token``.  Falcon
        tokens live for 30 minutes; the cache refreshes proactively 60 s before
        expiry, so a long sync cycle never uses a stale token.

        Raises:
            httpx.HTTPStatusError: If the token endpoint returns an error status.
            KeyError: If the token response has no ``access_token``.
        """
        if not self._token_cache.needs_refresh():
            return

        log.debug("Refreshing CrowdStrike access token", provider=self.name)
        response = await request_with_retry(
            self._client,
            "POST",
            _TOKEN_PATH,
            data={
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token_cache.set(
            data["access_token"], data.get("expires_in", _DEFAULT_TOKEN_TTL)
        )

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all hosts from the Falcon Hosts API.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.  Hosts with no serial number are skipped.

        Raises:
            RuntimeError: If called before a successful :meth:`authenticate`.
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        if not self._token_cache.token:
            raise RuntimeError(
                "CrowdStrike token missing — authenticate() must be called first"
            )

        headers = {"Authorization": f"Bearer {self._token_cache.token}"}

        host_ids = await self._scroll_host_ids(headers)
        if not host_ids:
            log.info("CrowdStrike hosts fetched", provider=self.name, count=0)
            return []

        raw_hosts = await self._fetch_host_details(host_ids, headers)

        online_states: dict[str, str] = {}
        if self._config.compliance.require_live and raw_hosts:
            online_states = await self._fetch_online_states(
                [str(raw.get("device_id") or "") for raw in raw_hosts], headers
            )

        devices = [self._build_device(raw, online_states) for raw in raw_hosts]
        log.info(
            "CrowdStrike hosts fetched",
            provider=self.name,
            count=len(devices),
            host_ids=len(host_ids),
        )
        return devices

    async def _scroll_host_ids(self, headers: dict[str, str]) -> list[str]:
        """Drain the scroll endpoint into a flat list of host IDs (AIDs).

        The scroll offset pointer expires after two minutes with no activity,
        so every page is requested back-to-back with no host-detail calls in
        between.

        Args:
            headers: Authorization header for the request.

        Returns:
            Every host ID visible to the API credential.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        host_ids: list[str] = []
        offset: str | None = None

        for _page_num in range(_MAX_PAGES):
            params: dict[str, str | int] = {"limit": _SCROLL_LIMIT}
            if offset:
                params["offset"] = offset

            response = await request_with_retry(
                self._client, "GET", _SCROLL_PATH, headers=headers, params=params
            )
            response.raise_for_status()
            data = response.json()

            resources: list[str] = data.get("resources") or []
            host_ids.extend(resources)

            # The final page can return a non-empty offset token with zero
            # resources — stop on either signal.
            offset = ((data.get("meta") or {}).get("pagination") or {}).get("offset")
            if not resources or not offset:
                break
        else:
            log.warning(
                "CrowdStrike scroll pagination safety limit reached — "
                "results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        return host_ids

    async def _fetch_host_details(
        self, host_ids: list[str], headers: dict[str, str]
    ) -> list[dict]:
        """Fetch full host records for *host_ids*, batched.

        Hosts with no discoverable serial number are dropped here (and logged),
        so they never reach the optional online-state lookup.

        Args:
            host_ids: Host IDs from :meth:`_scroll_host_ids`.
            headers: Authorization header for the request.

        Returns:
            Raw host dicts that carry a usable serial number.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        raw_hosts: list[dict] = []

        for batch in _chunks(host_ids, _DETAILS_BATCH):
            response = await request_with_retry(
                self._client,
                "POST",
                _DETAILS_PATH,
                headers=headers,
                json={"ids": batch},
            )
            response.raise_for_status()

            for raw in response.json().get("resources") or []:
                if _extract_serial(raw):
                    raw_hosts.append(raw)
                else:
                    log.debug(
                        "CrowdStrike host missing serial — skipping",
                        provider=self.name,
                        hostname=raw.get("hostname"),
                        device_id=raw.get("device_id"),
                    )

        return raw_hosts

    async def _fetch_online_states(
        self, host_ids: list[str], headers: dict[str, str]
    ) -> dict[str, str]:
        """Fetch live sensor state for *host_ids* (``compliance.require_live``).

        Args:
            host_ids: Host IDs to look up.
            headers: Authorization header for the request.

        Returns:
            Mapping of ``host_id -> state`` where state is ``"online"``,
            ``"offline"`` or ``"unknown"``.  Hosts absent from the response are
            absent from the mapping.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        states: dict[str, str] = {}

        for batch in _chunks([hid for hid in host_ids if hid], _ONLINE_STATE_BATCH):
            response = await request_with_retry(
                self._client,
                "GET",
                _ONLINE_STATE_PATH,
                headers=headers,
                params={"ids": batch},
            )
            response.raise_for_status()

            for entry in response.json().get("resources") or []:
                host_id = entry.get("id")
                if host_id:
                    states[host_id] = entry.get("state") or "unknown"

        return states

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from CrowdStrike host state.

        Two independently configurable checks, both enabled by default:

        * ``require_not_contained`` — ``status`` must not be ``contained`` or
          ``containment_pending``.  Those values mean an analyst has network-
          isolated the host, which is the strongest possible signal not to
          trust it.  ``normal`` and ``lifted_containment`` pass.
        * ``require_full_sensor`` — ``reduced_functionality_mode`` must not be
          ``yes``.  RFM means the sensor is running degraded.

        Both checks fail **open** on an absent or unrecognised value:
        ``reduced_functionality_mode`` is frequently missing on macOS and Linux
        hosts, and a missing signal is not evidence of a failing device.

        Args:
            device: Raw Falcon host record from ``/devices/entities/devices/v2``.

        Returns:
            ``True`` unless an enabled check sees an explicit failing value.
        """
        compliance = self._config.compliance

        if compliance.require_not_contained:
            status = str(device.get("status") or "").strip().lower()
            if status in _CONTAINED_STATUSES:
                return False

        if compliance.require_full_sensor:
            rfm = str(device.get("reduced_functionality_mode") or "").strip().lower()
            if rfm == "yes":
                return False

        return True

    def _build_device(
        self, device: dict, online_states: dict[str, str]
    ) -> ProviderDevice:
        """Convert a raw Falcon host dict to a :class:`ProviderDevice`.

        Args:
            device: Raw host record from the Falcon Hosts API.
            online_states: ``host_id -> state`` mapping, empty unless
                ``compliance.require_live`` is set.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        last_seen: datetime | None = None
        last_seen_raw = device.get("last_seen")
        if last_seen_raw:
            try:
                last_seen = datetime.fromisoformat(
                    str(last_seen_raw).replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        if self._config.compliance.require_live:
            is_online = online_states.get(str(device.get("device_id") or "")) == "online"
        else:
            # A sensor that has ever checked in counts as online; recency is
            # handled centrally by trust.max_days_since_checkin.
            is_online = last_seen is not None

        return ProviderDevice(
            serial_number=_extract_serial(device),
            hostname=device.get("hostname"),
            os_name=device.get("platform_name"),
            os_version=device.get("os_version"),
            is_online=is_online,
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )


def _extract_serial(device: dict) -> str:
    """Return the normalised serial number for a raw Falcon host record.

    Args:
        device: Raw host record from the Falcon Hosts API.

    Returns:
        ``serial_number.strip().upper()``, or ``""`` when the host reports no
        serial (common for some virtual machines).
    """
    return str(device.get("serial_number") or "").strip().upper()
