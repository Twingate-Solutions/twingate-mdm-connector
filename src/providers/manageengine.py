"""ManageEngine Endpoint Central provider plugin.

Supports two authentication variants:

  onprem  — Static API token (generated in Admin → Integrations → API Explorer).
            Header: ``Authorization: {api_token}``

  cloud   — Zoho OAuth2 refresh-token flow.
            Token endpoint: ``https://accounts.zoho.com/oauth/v2/token``
            Header: ``Authorization: Zoho-oauthtoken {access_token}``

Device data is assembled from two API calls per sync cycle:

  1. ``GET /api/1.4/desktop/computers``  — managed computers with agent status.
  2. ``GET /dcapi/inventory/complist``    — hardware inventory including serial numbers.

The two lists are joined by computer name (case-insensitive).  Computers
without a matching inventory entry (i.e. no serial number) are skipped.
"""

from datetime import UTC, datetime

import structlog

from src.config import ManageEngineConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

_ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
_CLOUD_BASE_URL = "https://endpointcentral.manageengine.com"
_PAGE_LIMIT = 200
_MAX_PAGES = 500

# On-prem: managed_status string values that indicate an active/healthy agent
_ACTIVE_STATUSES = frozenset({"ACTIVE", "MANAGED"})

# Cloud: installation_status value that indicates agent is installed and managed
_CLOUD_INSTALLED_STATUS = 22

# Cloud: computer_live_status value that indicates machine is reachable
_CLOUD_LIVE_STATUS = 1


class ManageEngineProvider(ProviderPlugin):
    """ManageEngine Endpoint Central provider plugin.

    Supports both on-premises deployments (static API token) and cloud
    deployments (Zoho OAuth2 via refresh token).  Device records are
    assembled by joining the computers list with hardware inventory data.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "manageengine"

    def __init__(self, config: ManageEngineConfig) -> None:
        """Initialise the ManageEngine provider.

        Args:
            config: ManageEngine configuration from YAML / env.
        """
        self._config = config
        self._token_cache = TokenCache()  # used only for cloud variant

        if config.variant == "onprem":
            base_url = (config.base_url or "").rstrip("/")
        else:
            base_url = (config.base_url or _CLOUD_BASE_URL).rstrip("/")

        self._base_url = base_url
        self._client = build_client(base_url=base_url)

    def _auth_headers(self) -> dict[str, str]:
        """Return the correct ``Authorization`` header for the active variant."""
        if self._config.variant == "onprem":
            return {"Authorization": self._config.api_token or ""}
        return {"Authorization": f"Zoho-oauthtoken {self._token_cache.token}"}

    async def authenticate(self) -> None:
        """Obtain or refresh credentials.

        * **on-prem**: no-op — the static API token is used directly.
        * **cloud**: exchanges the Zoho refresh token for a new access token
          when the cached token is missing or close to expiry.

        Raises:
            httpx.HTTPStatusError: If the Zoho token endpoint returns an error.
        """
        if self._config.variant == "onprem":
            return

        if not self._token_cache.needs_refresh():
            return

        token_url = self._config.oauth_token_url or _ZOHO_TOKEN_URL
        log.debug("Refreshing ManageEngine Zoho access token", provider=self.name)
        response = await request_with_retry(
            self._client,
            "POST",
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._config.oauth_client_id,
                "client_secret": self._config.oauth_client_secret,
                "refresh_token": self._config.oauth_refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token_cache.set(data["access_token"], data.get("expires_in", 3600))
        log.debug("ManageEngine Zoho access token refreshed", provider=self.name)

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all managed computers.

        **Cloud variant** performs a single paginated call to
        ``/api/1.4/som/computers``, which includes serial numbers inline.

        **On-prem variant** performs two paginated API calls and joins the
        results by computer name:

        1. ``/api/1.4/desktop/computers`` — agent status, OS info, last contact.
        2. ``/dcapi/inventory/complist``   — serial numbers.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        headers = self._auth_headers()

        if self._config.variant == "cloud":
            return await self._list_cloud_devices(headers)

        # on-prem: two-call join
        computers = await self._fetch_computers(headers)
        serials = await self._fetch_inventory_serials(headers)

        devices: list[ProviderDevice] = []
        for comp in computers:
            name_key = (comp.get("computer_name") or "").strip().upper()
            serial = serials.get(name_key, "")
            if not serial:
                log.debug(
                    "ManageEngine computer has no inventory serial — skipping",
                    provider=self.name,
                    computer_name=comp.get("computer_name"),
                )
                continue
            devices.append(self._build_device(comp, serial))

        log.info("ManageEngine computers fetched", provider=self.name, count=len(devices))
        return devices

    async def _list_cloud_devices(self, headers: dict[str, str]) -> list[ProviderDevice]:
        """Fetch all managed computers from the cloud SOM endpoint.

        The cloud ``/api/1.4/som/computers`` response embeds the serial number
        (``managedcomputerextn.service_tag``) directly, so no second API call
        is required.

        Args:
            headers: Auth headers for the request.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.
        """
        devices: list[ProviderDevice] = []
        page = 1

        for _page_num in range(_MAX_PAGES):
            response = await request_with_retry(
                self._client,
                "GET",
                "/api/1.4/som/computers",
                headers=headers,
                params={"page": page, "pagelimit": _PAGE_LIMIT},
            )
            response.raise_for_status()
            data = response.json()

            payload = data.get("message_response") or data
            page_computers: list[dict] = payload.get("computers") or []

            for comp in page_computers:
                serial = (comp.get("managedcomputerextn.service_tag") or "").strip()
                if not serial:
                    log.debug(
                        "ManageEngine cloud computer has no service_tag serial — skipping",
                        provider=self.name,
                        computer_name=comp.get("full_name"),
                    )
                    continue
                devices.append(self._build_cloud_device(comp, serial))

            total: int = payload.get("total") or 0
            if len(page_computers) < _PAGE_LIMIT or (total and len(devices) >= total):
                break
            page += 1
        else:
            log.warning(
                "ManageEngine cloud computers pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        log.info("ManageEngine cloud computers fetched", provider=self.name, count=len(devices))
        return devices

    async def _fetch_computers(self, headers: dict[str, str]) -> list[dict]:
        """Paginate ``/api/1.4/desktop/computers`` and return all records."""
        computers: list[dict] = []
        page = 1

        for _page_num in range(_MAX_PAGES):
            response = await request_with_retry(
                self._client,
                "GET",
                "/api/1.4/desktop/computers",
                headers=headers,
                params={"page": page, "pagelimit": _PAGE_LIMIT},
            )
            response.raise_for_status()
            data = response.json()

            # ManageEngine wraps response in message_response on some versions
            payload = data.get("message_response") or data
            page_computers: list[dict] = payload.get("computers") or []
            computers.extend(page_computers)

            total: int = payload.get("computers_count") or 0
            if len(page_computers) < _PAGE_LIMIT or (total and len(computers) >= total):
                break
            page += 1
        else:
            log.warning(
                "ManageEngine computers pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        return computers

    async def _fetch_inventory_serials(
        self, headers: dict[str, str]
    ) -> dict[str, str]:
        """Paginate ``/dcapi/inventory/complist`` and return a name→serial map."""
        serials: dict[str, str] = {}
        page = 1

        for _page_num in range(_MAX_PAGES):
            response = await request_with_retry(
                self._client,
                "GET",
                "/dcapi/inventory/complist",
                headers=headers,
                params={"page": page, "pagelimit": _PAGE_LIMIT},
            )
            response.raise_for_status()
            data = response.json()

            comp_details: list[dict] = data.get("compdetails") or []
            for item in comp_details:
                # sysinfo sub-object holds the hardware fields
                sysinfo = item.get("sysinfo") or item
                name = (
                    sysinfo.get("COMPNAME")
                    or sysinfo.get("compname")
                    or ""
                ).strip().upper()
                serial = (
                    sysinfo.get("SERIALNUMBER")
                    or sysinfo.get("serial_number")
                    or sysinfo.get("BIOS_SERIALNUMBER")
                    or ""
                )
                if name and serial:
                    serials[name] = serial

            total: int = data.get("compcount") or 0
            if len(comp_details) < _PAGE_LIMIT or (total and len(serials) >= total):
                break
            page += 1
        else:
            log.warning(
                "ManageEngine inventory pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        return serials

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from the agent's managed status.

        **On-prem:** checks ``managed_status`` string against ``ACTIVE`` /
        ``MANAGED``.

        **Cloud:** applies the checks enabled in ``config.compliance``:

        * ``require_installed`` (default ``True``) — ``installation_status``
          must be ``22`` (agent installed and managed).
        * ``require_live`` (default ``False``) — ``computer_live_status``
          must be ``1`` (machine is currently reachable by the agent).

        Args:
            device: Raw ManageEngine computer object.

        Returns:
            ``True`` if the device passes all enabled compliance checks.
        """
        if self._config.variant == "cloud":
            c = self._config.compliance
            if c.require_installed:
                if int(device.get("installation_status") or 0) != _CLOUD_INSTALLED_STATUS:
                    return False
            if c.require_live:
                if int(device.get("computer_live_status") or 0) != _CLOUD_LIVE_STATUS:
                    return False
            return True

        status = (
            device.get("managed_status")
            or device.get("managedStatus")
            or ""
        ).strip().upper()
        return status in _ACTIVE_STATUSES

    def _build_cloud_device(self, computer: dict, serial: str) -> ProviderDevice:
        """Build a :class:`ProviderDevice` from a cloud SOM computer record.

        The cloud ``/api/1.4/som/computers`` response uses different field
        names from the on-prem API: ``full_name`` for hostname,
        ``agent_last_contact_time`` for the last-seen timestamp, and
        ``os_version`` / ``os_platform_name`` for OS details.

        Args:
            computer: Raw computer object from ``/api/1.4/som/computers``.
            serial: Serial number from ``managedcomputerextn.service_tag``.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        last_seen: datetime | None = None
        last_contact = computer.get("agent_last_contact_time")
        if last_contact:
            try:
                ts_ms = int(last_contact)
                last_seen = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            except (ValueError, TypeError, OSError):
                pass

        is_active = self.determine_compliance(computer)

        return ProviderDevice(
            serial_number=serial.strip().upper(),
            hostname=computer.get("full_name") or computer.get("fqdn_name"),
            os_name=computer.get("os_platform_name"),
            os_version=computer.get("os_version"),
            is_online=is_active,
            is_compliant=is_active,
            last_seen=last_seen,
            raw=computer,
        )

    def _build_device(self, computer: dict, serial: str) -> ProviderDevice:
        """Build a :class:`ProviderDevice` from a computer record + serial.

        Args:
            computer: Raw computer object from ``/api/1.4/desktop/computers``.
            serial: Serial number resolved from the inventory endpoint.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        last_seen: datetime | None = None
        last_contact = computer.get("last_contact_time")
        if last_contact:
            try:
                # ManageEngine stores timestamps as epoch milliseconds
                ts_ms = int(last_contact)
                last_seen = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            except (ValueError, TypeError, OSError):
                pass

        is_active = self.determine_compliance(computer)

        return ProviderDevice(
            serial_number=serial.strip().upper(),
            hostname=computer.get("computer_name"),
            os_name=computer.get("os_name"),
            os_version=None,
            is_online=is_active,
            is_compliant=is_active,
            last_seen=last_seen,
            raw=computer,
        )
