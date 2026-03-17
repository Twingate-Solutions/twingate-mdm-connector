"""Mosyle provider plugin.

Auth:    Access token + email + password embedded in every POST request body.
         No separate authentication step is required.
API:     POST-based (list operations use POST, not GET).
Base:    https://managerapi.mosyle.com/v2  (Manager)
         https://businessapi.mosyle.com/v2 (Business)
Docs:    Enable at Organization → API Integration.

Device types fetched: macOS (os=osx) and iOS/iPadOS (os=ios).
Pagination: ``"page": N`` in request body (1-indexed).
            Stop when the ``devices`` array is empty.
Note:    Apple-only platform — will only match macOS, iOS, and iPadOS devices.
"""

from datetime import UTC, datetime

import structlog

from src.config import MosyleConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import build_client, request_with_retry

log = structlog.get_logger()

_MANAGER_BASE = "https://managerapi.mosyle.com/v2"
_BUSINESS_BASE = "https://businessapi.mosyle.com/v2"

# OS types to query; Mosyle separates macOS from iOS/iPadOS
_OS_TYPES = ("osx", "ios")
_MAX_PAGES = 500


class MosyleProvider(ProviderPlugin):
    """Mosyle Apple MDM provider plugin.

    Fetches managed macOS and iOS/iPadOS devices from Mosyle Manager or
    Mosyle Business.  Authentication credentials are embedded in each POST
    request body — no separate token acquisition is required.

    Compliance is determined by device enrollment status and recency of the
    last check-in (``date_last_beat``).
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "mosyle"

    def __init__(self, config: MosyleConfig) -> None:
        """Initialise the Mosyle provider.

        Args:
            config: Mosyle configuration from YAML / env.  Set
                ``is_business: true`` to target the Business API endpoint.
        """
        self._config = config
        base_url = _BUSINESS_BASE if config.is_business else _MANAGER_BASE
        self._client = build_client(base_url=base_url)

    def _auth_body(self) -> dict:
        """Return the authentication fields included in every POST body."""
        return {
            "accessToken": self._config.access_token,
            "email": self._config.email,
            "password": self._config.password,
        }

    async def authenticate(self) -> None:
        """No-op — Mosyle embeds credentials in every POST request body."""

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all Apple devices from Mosyle (macOS + iOS/iPadOS).

        For each OS type, paginates using the ``page`` field in the POST body
        (1-indexed) until an empty ``devices`` array is returned.  Devices
        without a serial number are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []

        for os_type in _OS_TYPES:
            page = 1
            for _page_num in range(_MAX_PAGES):
                body = {
                    **self._auth_body(),
                    "options": {"os": os_type, "page": page},
                }
                response = await request_with_retry(
                    self._client,
                    "POST",
                    "/listdevices",
                    json=body,
                )
                response.raise_for_status()
                data = response.json()

                page_devices = self._extract_devices(data)
                if not page_devices:
                    break

                for raw in page_devices:
                    device = self._build_device(raw, os_type)
                    if device.serial_number:
                        devices.append(device)
                    else:
                        log.debug(
                            "Mosyle device missing serial — skipping",
                            provider=self.name,
                            device_name=raw.get("device_name"),
                            os_type=os_type,
                        )

                page += 1
            else:
                log.warning(
                    "Mosyle pagination safety limit reached — results may be incomplete",
                    provider=self.name,
                    os_type=os_type,
                    max_pages=_MAX_PAGES,
                )

        log.info("Mosyle devices fetched", provider=self.name, count=len(devices))
        return devices

    def _extract_devices(self, data: dict) -> list[dict]:
        """Extract the device list from a Mosyle API response.

        Handles both the nested ``response[0].devices`` format and a flat
        top-level ``devices`` key.

        Args:
            data: Parsed JSON response from the Mosyle API.

        Returns:
            List of raw device dicts, or an empty list if none found.
        """
        # Format A: {"status": "OK", "response": [{"devices": [...], ...}]}
        response_list = data.get("response")
        if isinstance(response_list, list) and response_list:
            inner = response_list[0]
            if isinstance(inner, dict):
                return inner.get("devices") or []

        # Format B: {"status": "OK", "devices": [...]}
        return data.get("devices") or []

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from enrollment status and last check-in.

        A device is compliant when it is enrolled/managed in Mosyle.
        The ``date_last_beat`` recency is handled by the engine via
        ``max_days_since_checkin`` rather than here.

        Args:
            device: Raw Mosyle device object from the API.

        Returns:
            ``True`` if the device is in an enrolled/managed state.
        """
        status = (device.get("status") or "").lower()
        return status in ("enrolled", "managed", "supervised")

    def _build_device(self, device: dict, os_type: str) -> ProviderDevice:
        """Convert a raw Mosyle device dict to a :class:`ProviderDevice`.

        Args:
            device: Raw device object from the Mosyle API.
            os_type: One of ``"osx"`` or ``"ios"`` — used for os_name fallback.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("serial_number") or ""

        last_seen: datetime | None = None
        last_beat = device.get("date_last_beat")
        if last_beat:
            try:
                # Mosyle returns epoch seconds as a string or int
                last_seen = datetime.fromtimestamp(int(last_beat), tz=UTC)
            except (ValueError, TypeError, OSError):
                # Try ISO format fallback
                try:
                    last_seen = datetime.fromisoformat(
                        str(last_beat).replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass

        os_name_map = {"osx": "macOS", "ios": "iOS/iPadOS"}

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("device_name"),
            os_name=device.get("os_version") or os_name_map.get(os_type),
            os_version=device.get("os_version"),
            is_online=True,  # Mosyle doesn't expose a direct online/offline flag
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
