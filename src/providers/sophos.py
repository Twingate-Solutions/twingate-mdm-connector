"""Sophos Central provider plugin.

Auth flow (two hops):
  1. POST https://id.sophos.com/api/v2/oauth2/token  → Bearer access token
  2. GET  https://api.central.sophos.com/whoami/v1   → tenant ID + regional API base
  3. All subsequent calls use regional base + X-Tenant-ID header.

API:  /endpoint/v1/endpoints  (paginated via pageFromKey cursor)
Docs: https://developer.sophos.com/apis
"""

from datetime import UTC, datetime

import structlog

from src.config import SophosConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

_TOKEN_URL = "https://id.sophos.com/api/v2/oauth2/token"
_WHOAMI_URL = "https://api.central.sophos.com/whoami/v1"
_PAGE_SIZE = 500
_MAX_PAGES = 500


class SophosProvider(ProviderPlugin):
    """Sophos Central provider plugin.

    Implements the two-hop auth flow: OAuth2 token acquisition followed by
    tenant discovery via ``/whoami/v1``, which returns the regional API base
    URL and tenant ID required for all subsequent calls.

    Compliance is determined by the ``health.overall`` field on each endpoint
    object (value ``"good"`` = compliant).

    Serial numbers are extracted from a prioritised set of field paths in the
    endpoint response.  Endpoints where no serial can be found are skipped.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "sophos"

    def __init__(self, config: SophosConfig) -> None:
        """Initialise the Sophos provider.

        Args:
            config: Sophos configuration from YAML / env.
        """
        self._config = config
        self._token_cache = TokenCache()
        self._tenant_id: str | None = None
        self._api_base: str | None = None
        self._client = build_client()

    async def authenticate(self) -> None:
        """Obtain OAuth2 token and discover tenant API base URL.

        Two-hop flow:

        1. POST client credentials to ``id.sophos.com`` for a Bearer token.
        2. GET ``/whoami/v1`` to discover the tenant ID and regional API URL.

        Skips both hops if the cached token is still valid and tenant info is
        already known.

        Raises:
            httpx.HTTPStatusError: On auth or discovery endpoint errors.
            KeyError: If the whoami response is missing expected fields.
        """
        if not self._token_cache.needs_refresh() and self._tenant_id:
            return

        log.debug("Refreshing Sophos access token", provider=self.name)
        token_resp = await request_with_retry(
            self._client,
            "POST",
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": "token",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token: str = token_data["access_token"]
        self._token_cache.set(access_token, token_data.get("expires_in", 3600))

        log.debug("Discovering Sophos tenant", provider=self.name)
        whoami_resp = await request_with_retry(
            self._client,
            "GET",
            _WHOAMI_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        whoami_resp.raise_for_status()
        whoami = whoami_resp.json()

        self._tenant_id = whoami["id"]
        self._api_base = whoami["apiHosts"]["dataRegion"]
        log.debug(
            "Sophos tenant discovered",
            provider=self.name,
            tenant_id=self._tenant_id,
            api_base=self._api_base,
        )

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all endpoints from Sophos Central (cursor-paginated).

        Uses ``pageFromKey`` cursor returned in ``pages.nextKey`` to exhaust
        all pages.  Endpoints without a discoverable serial number are skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            RuntimeError: If called before a successful :meth:`authenticate`.
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        if not self._tenant_id or not self._api_base:
            raise RuntimeError(
                "Sophos tenant not discovered — authenticate() must be called first"
            )

        devices: list[ProviderDevice] = []
        next_key: str | None = None
        headers = {
            "Authorization": f"Bearer {self._token_cache.token}",
            "X-Tenant-ID": self._tenant_id,
        }

        for _page_num in range(_MAX_PAGES):
            params: dict[str, str | int] = {"pageSize": _PAGE_SIZE}
            if next_key:
                params["pageFromKey"] = next_key

            response = await request_with_retry(
                self._client,
                "GET",
                f"{self._api_base}/endpoint/v1/endpoints",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            for raw in data.get("items", []):
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "Sophos endpoint missing serial — skipping",
                        provider=self.name,
                        hostname=raw.get("hostname"),
                        endpoint_id=raw.get("id"),
                    )

            next_key = (data.get("pages") or {}).get("nextKey")
            if not next_key:
                break
        else:
            log.warning(
                "Sophos pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        log.info("Sophos endpoints fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from the Sophos endpoint health status.

        A device is compliant when ``health.overall == "good"``.

        Args:
            device: Raw Sophos endpoint object from the API.

        Returns:
            ``True`` if ``health.overall`` is ``"good"``.
        """
        health = device.get("health") or {}
        return health.get("overall") == "good"

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw Sophos endpoint dict to a :class:`ProviderDevice`.

        Tries multiple field paths to find the serial number:

        1. Top-level ``serialNumber``
        2. ``os.serialNumber``
        3. ``metadata.computerSerial``

        Args:
            device: Raw endpoint object from the Sophos API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = (
            device.get("serialNumber")
            or (device.get("os") or {}).get("serialNumber")
            or (device.get("metadata") or {}).get("computerSerial")
            or ""
        )

        last_seen: datetime | None = None
        last_seen_raw = device.get("lastSeenAt")
        if last_seen_raw:
            try:
                last_seen = datetime.fromisoformat(
                    last_seen_raw.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        health = device.get("health") or {}
        services = health.get("services") or {}
        # Treat endpoint as online if its service health is reported as good
        services_status = services.get("status") or services.get("summary") or ""
        is_online = services_status == "good"

        os_info = device.get("os") or {}

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("hostname"),
            os_name=os_info.get("platform") or os_info.get("name"),
            os_version=str(os_info["majorVersion"]) if os_info.get("majorVersion") else None,
            is_online=is_online,
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
