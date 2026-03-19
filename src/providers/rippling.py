"""Rippling provider plugin.

Auth:   OAuth2 Client Credentials — POST client_id + client_secret to
        ``https://api.rippling.com/api/o/token/`` with
        ``grant_type=client_credentials``.
        Access tokens are short-lived (~1 hour); cached and refreshed via
        :class:`~src.utils.http.TokenCache`.

API:    ``https://api.rippling.com/platform/api``
Docs:   https://developer.rippling.com/

Device endpoint: ``GET /platform/api/devices``
Pagination:      ``next`` field in response (full URL); follow until ``null``.
Serial field:    ``serialNumber``
Compliance:      ``managementStatus`` must be ``"ACTIVE"`` or ``"MANAGED"``.
                 Devices with any other status (e.g. ``"PENDING"``,
                 ``"INACTIVE"``) are treated as non-compliant.
Online status:   Derived from ``managementStatus`` — a device is considered
                 online when ``managementStatus`` is ``"ACTIVE"`` or
                 ``"MANAGED"``.
"""

from datetime import UTC, datetime
from urllib.parse import urlparse

import structlog

from src.config import RipplingConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import TokenCache, build_client, request_with_retry

log = structlog.get_logger()

_BASE_URL = "https://api.rippling.com"
_TOKEN_URL = f"{_BASE_URL}/api/o/token/"
_DEVICES_URL = f"{_BASE_URL}/platform/api/devices"
_EXPECTED_HOST = "api.rippling.com"

# managementStatus values that indicate a healthy, actively-managed device
_ACTIVE_STATUSES = frozenset({"ACTIVE", "MANAGED"})
_MAX_PAGES = 500


class RipplingProvider(ProviderPlugin):
    """Rippling HR/IT platform provider plugin.

    Authenticates via OAuth2 client credentials, then paginates
    ``GET /platform/api/devices`` until all device records have been
    collected.  Compliance is determined by ``managementStatus``.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "rippling"

    def __init__(self, config: RipplingConfig) -> None:
        """Initialise the Rippling provider.

        Args:
            config: Rippling configuration from YAML / env.
        """
        self._config = config
        self._token_cache = TokenCache()
        self._client = build_client(base_url=_BASE_URL)

    async def authenticate(self) -> None:
        """Obtain or refresh the OAuth2 client-credentials access token.

        The token is cached and refreshed proactively before expiry (60 s
        margin).  Rippling tokens are typically valid for 3600 seconds.

        Raises:
            httpx.HTTPStatusError: If the token endpoint returns an error.
        """
        if not self._token_cache.needs_refresh():
            return

        log.debug("Refreshing Rippling access token", provider=self.name)
        response = await request_with_retry(
            self._client,
            "POST",
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token_cache.set(data["access_token"], data.get("expires_in", 3600))
        log.debug("Rippling access token refreshed", provider=self.name)

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all devices from Rippling by following the ``next`` cursor.

        Starts at ``/platform/api/devices`` and follows the ``next`` URL in
        each response until it is ``null``.  Devices without a serial number
        are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []
        headers = {"Authorization": f"Bearer {self._token_cache.token}"}
        next_url: str | None = _DEVICES_URL
        page_count = 0

        while next_url:
            page_count += 1
            if page_count > _MAX_PAGES:
                log.warning(
                    "Rippling pagination safety limit reached — results may be incomplete",
                    provider=self.name,
                    max_pages=_MAX_PAGES,
                )
                break
            parsed_host = urlparse(next_url).hostname or ""
            if parsed_host != _EXPECTED_HOST:
                raise ValueError(
                    f"Rippling pagination URL has unexpected host "
                    f"{parsed_host!r} (expected {_EXPECTED_HOST!r})"
                )
            response = await request_with_retry(
                self._client,
                "GET",
                next_url,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            # Rippling may return a list directly or a paginated envelope
            # with ``results`` and ``next``.
            if isinstance(data, list):
                results = data
                next_url = None
            else:
                results = data.get("results") or []
                next_url = data.get("next")

            for raw in results:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "Rippling device missing serial — skipping",
                        provider=self.name,
                        device_id=raw.get("id"),
                        device_name=raw.get("name"),
                    )

        log.info("Rippling devices fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from ``managementStatus``.

        A device is compliant when ``managementStatus`` is ``"ACTIVE"`` or
        ``"MANAGED"``.  Any other value (``"PENDING"``, ``"INACTIVE"``,
        absent) is treated as non-compliant.

        Args:
            device: Raw Rippling device object from the API.

        Returns:
            ``True`` if the device is actively managed.
        """
        status = (device.get("managementStatus") or "").upper()
        return status in _ACTIVE_STATUSES

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw Rippling device dict to a :class:`ProviderDevice`.

        Args:
            device: Raw device object from the Rippling API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("serialNumber") or ""

        last_seen: datetime | None = None
        last_seen_raw = device.get("lastSeen") or device.get("lastCheckin")
        if last_seen_raw:
            try:
                last_seen = datetime.fromisoformat(
                    last_seen_raw.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        status = (device.get("managementStatus") or "").upper()
        is_online = status in _ACTIVE_STATUSES

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("name") or device.get("hostname"),
            os_name=device.get("osType") or device.get("operatingSystem"),
            os_version=device.get("osVersion"),
            is_online=is_online,
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
