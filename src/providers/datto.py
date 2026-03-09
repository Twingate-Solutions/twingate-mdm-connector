"""Datto RMM provider plugin.

Auth:   OAuth2 password grant — POST ``api_key`` (username) + ``api_secret``
        (password) to ``{api_url}/auth/oauth/token``.
        Access token valid for ~100 hours (360 000 s).
API:    ``{api_url}/api/v2``
Docs:   Swagger at ``{api_url}/api/v2/swagger-ui.html``

Regions: pinotage, merlot, concord, vidal, syrah, zinfandel
         (customer-specific — supplied via ``api_url`` in config).

Pagination:  ``pageDetails.nextPageUrl`` in response; follow until ``null``.
             Max 250 devices per page.
Compliance:  ``patchStatus`` + ``antivirusStatus`` + ``rebootRequired``.
Rate limit:  600 reads/min; on 429 wait 60 s (handled by request_with_retry).
"""

from datetime import UTC, datetime

import structlog

from src.config import DattoConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

# Patch / AV status values that indicate a compliant device
_GOOD_PATCH_STATUSES = frozenset({"FULLY_PATCHED", "NOT_SUPPORTED", "UP_TO_DATE"})
_GOOD_AV_STATUSES = frozenset({"PROTECTED", "NOT_APPLICABLE", "NONE"})


class DattoProvider(ProviderPlugin):
    """Datto RMM provider plugin.

    Authenticates via OAuth2 password grant using the customer's API key and
    secret, then paginates ``/api/v2/account/devices`` until all device records
    have been collected.

    Compliance is evaluated from patch status, antivirus status, and reboot
    requirement reported by the Datto agent.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "datto"

    def __init__(self, config: DattoConfig) -> None:
        """Initialise the Datto RMM provider.

        Args:
            config: Datto configuration from YAML / env.  ``api_url`` must
                be the regional base URL including scheme but without a
                trailing slash (e.g. ``https://pinotage-api.centrastage.net``).
        """
        self._config = config
        self._token_cache = TokenCache()
        base_url = config.api_url.rstrip("/")
        self._base_url = base_url
        self._token_url = f"{base_url}/auth/oauth/token"
        self._client = build_client(base_url=base_url)

    async def authenticate(self) -> None:
        """Obtain or refresh the OAuth2 access token.

        Uses the *password* grant type with the API key as username and the
        API secret as password.  The token is cached and refreshed proactively
        before expiry (default 60 s margin, token valid for ~100 h).

        Raises:
            httpx.HTTPStatusError: If the token endpoint returns an error.
        """
        if not self._token_cache.needs_refresh():
            return

        log.debug("Refreshing Datto RMM access token", provider=self.name)
        response = await request_with_retry(
            self._client,
            "POST",
            self._token_url,
            data={
                "grant_type": "password",
                "username": self._config.api_key,
                "password": self._config.api_secret,
                "scope": "user_impersonation",
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token_cache.set(data["access_token"], data.get("expires_in", 360_000))
        log.debug("Datto RMM access token refreshed", provider=self.name)

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all devices from Datto RMM by following ``nextPageUrl``.

        Starts at ``/api/v2/account/devices`` and follows
        ``pageDetails.nextPageUrl`` until it is ``null``.  Devices without a
        serial number are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []
        headers = {"Authorization": f"Bearer {self._token_cache.token}"}
        next_url: str | None = f"{self._base_url}/api/v2/account/devices"

        while next_url:
            response = await request_with_retry(
                self._client,
                "GET",
                next_url,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            for raw in data.get("devices") or []:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "Datto device missing serial — skipping",
                        provider=self.name,
                        device_hostname=raw.get("hostname"),
                        device_uid=raw.get("uid"),
                    )

            next_url = (data.get("pageDetails") or {}).get("nextPageUrl")

        log.info("Datto RMM devices fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from patch status, AV status, and reboot flag.

        A device is compliant when:

        * ``patchStatus`` indicates fully patched or not applicable.
        * ``antivirusStatus`` indicates protected or not applicable.
        * ``rebootRequired`` is ``False`` (or absent).

        Args:
            device: Raw Datto device object from the API.

        Returns:
            ``True`` if the device passes all compliance checks.
        """
        patch_status = (device.get("patchStatus") or "").upper()
        av_status = (device.get("antivirusStatus") or "").upper()

        if patch_status and patch_status not in _GOOD_PATCH_STATUSES:
            return False
        if av_status and av_status not in _GOOD_AV_STATUSES:
            return False
        if device.get("rebootRequired", False):
            return False

        return True

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw Datto device dict to a :class:`ProviderDevice`.

        Args:
            device: Raw device object from the Datto RMM API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("serialNumber") or ""

        last_seen: datetime | None = None
        last_seen_raw = device.get("lastSeen")
        if last_seen_raw:
            try:
                last_seen = datetime.fromisoformat(
                    last_seen_raw.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("hostname"),
            os_name=device.get("operatingSystem"),
            os_version=None,
            is_online=device.get("online", False),
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
