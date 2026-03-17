"""NinjaOne (NinjaRMM) provider plugin.

Auth:   OAuth2 Client Credentials — client_id + client_secret, scope: monitoring management
Token:  POST https://{region}.ninjarmm.com/ws/oauth/token
API:    https://{region}.ninjarmm.com/api/v2
Docs:   https://app.ninjarmm.com/apidocs-beta/core-resources/operations/getDevices
"""

from datetime import UTC, datetime

import structlog

from src.config import NinjaOneConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

# Regional API base URLs
_REGION_BASES: dict[str, str] = {
    "app": "https://app.ninjarmm.com",
    "eu": "https://eu.ninjarmm.com",
    "ca": "https://ca.ninjarmm.com",
    "au": "https://au.ninjarmm.com",
    "oc": "https://oc.ninjarmm.com",
}

_PAGE_SIZE = 200
_MAX_PAGES = 500

# AV threat statuses that indicate a clean device
_CLEAN_THREAT_STATUSES = frozenset({"GOOD", "PROTECTED", "NOT_RUNNING", ""})

# Patch statuses that indicate non-compliance
_CRITICAL_PATCH_STATUSES = frozenset({"CRITICAL", "FAILED"})


class NinjaOneProvider(ProviderPlugin):
    """NinjaOne (NinjaRMM) provider plugin.

    Fetches all managed devices via the NinjaOne v2 REST API using OAuth2
    client credentials. Supports regional endpoints (US, EU, CA, AU, OC).

    Compliance is determined by antivirus threat status and patch status
    from the enriched ``/v2/devices-detailed`` endpoint.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "ninjaone"

    def __init__(self, config: NinjaOneConfig) -> None:
        """Initialise the NinjaOne provider.

        Args:
            config: NinjaOne configuration from YAML / env.
        """
        self._config = config
        self._token_cache = TokenCache()
        base_url = _REGION_BASES.get(config.region, f"https://{config.region}.ninjarmm.com")
        self._base_url = base_url
        self._token_url = f"{base_url}/ws/oauth/token"
        self._client = build_client(base_url=base_url)

    async def authenticate(self) -> None:
        """Obtain or refresh the OAuth2 access token.

        Uses the client credentials flow with scope ``monitoring management``.
        The token is cached and refreshed proactively 60 s before expiry.

        Raises:
            httpx.HTTPStatusError: If the token endpoint returns an error status.
        """
        if not self._token_cache.needs_refresh():
            return

        log.debug("Refreshing NinjaOne access token", provider=self.name)
        response = await request_with_retry(
            self._client,
            "POST",
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": "monitoring management",
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token_cache.set(data["access_token"], data.get("expires_in", 3600))
        log.debug("NinjaOne access token refreshed", provider=self.name)

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all devices from NinjaOne using cursor-based pagination.

        Uses ``/api/v2/devices-detailed`` for enriched compliance data
        (antivirus status, patch status).  Devices without a serial number
        are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []
        cursor: str | None = None
        headers = {"Authorization": f"Bearer {self._token_cache.token}"}

        for _page_num in range(_MAX_PAGES):
            params: dict[str, str | int] = {"pageSize": _PAGE_SIZE}
            if cursor:
                params["after"] = cursor

            response = await request_with_retry(
                self._client,
                "GET",
                "/api/v2/devices-detailed",
                headers=headers,
                params=params,
            )
            response.raise_for_status()

            page: list[dict] = response.json()
            if not page:
                break

            for raw in page:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "NinjaOne device missing serial — skipping",
                        provider=self.name,
                        device_name=raw.get("systemName"),
                        device_id=raw.get("id"),
                    )

            if len(page) < _PAGE_SIZE:
                break

            last_id = page[-1].get("id")
            if last_id is None:
                break
            cursor = str(last_id)
        else:
            log.warning(
                "NinjaOne pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        log.info("NinjaOne devices fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate device compliance from antivirus and patch status.

        A device is compliant when:

        * Antivirus threat status is in the clean set (or AV data absent).
        * Patch status is not ``CRITICAL`` or ``FAILED``.

        Args:
            device: Raw NinjaOne device object from the API.

        Returns:
            ``True`` if the device passes compliance checks.
        """
        antivirus = device.get("antivirus") or {}
        if antivirus.get("threatStatus", "GOOD") not in _CLEAN_THREAT_STATUSES:
            return False

        patches = device.get("patches") or {}
        if patches.get("patchStatus", "OK") in _CRITICAL_PATCH_STATUSES:
            return False

        return True

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw NinjaOne device dict to a :class:`ProviderDevice`.

        Args:
            device: Raw device object from the NinjaOne API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        system = device.get("system") or {}
        serial_raw: str = (
            system.get("serialNumber")
            or device.get("systemSerialNumber")
            or ""
        )

        last_seen: datetime | None = None
        last_contact = device.get("lastContact")
        if last_contact is not None:
            if isinstance(last_contact, (int, float)):
                try:
                    last_seen = datetime.fromtimestamp(last_contact, tz=UTC)
                except (OSError, OverflowError, ValueError):
                    pass
            elif isinstance(last_contact, str):
                try:
                    last_seen = datetime.fromisoformat(last_contact.replace("Z", "+00:00"))
                except ValueError:
                    pass

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=(
                device.get("dnsName")
                or device.get("systemName")
                or device.get("displayName")
            ),
            os_name=device.get("nodeClass"),
            os_version=None,
            is_online=not device.get("offline", False),
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
