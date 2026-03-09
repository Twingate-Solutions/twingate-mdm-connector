"""JumpCloud provider plugin.

Auth:  Static API key via ``x-api-key`` header — no OAuth flow required.
API:   https://console.jumpcloud.com/api  (v1)
Docs:  https://docs.jumpcloud.com/api/

Pagination (v1): ``skip`` + ``limit`` (max 100) with ``totalCount`` in response.
Compliance:      ``active`` agent status + optional FDE (disk encryption) check.
"""

from datetime import UTC, datetime

import structlog

from src.config import JumpCloudConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import build_client, request_with_retry

log = structlog.get_logger()

_API_BASE = "https://console.jumpcloud.com/api"
_PAGE_LIMIT = 100


class JumpCloudProvider(ProviderPlugin):
    """JumpCloud provider plugin.

    Fetches all managed systems from the JumpCloud v1 REST API using a static
    API key.  Compliance is determined by agent active status and, when present,
    Full Disk Encryption (FDE) state.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "jumpcloud"

    def __init__(self, config: JumpCloudConfig) -> None:
        """Initialise the JumpCloud provider.

        The API key is injected as a default ``x-api-key`` header so every
        request is authenticated without extra per-request configuration.

        Args:
            config: JumpCloud configuration from YAML / env.
        """
        self._config = config
        self._client = build_client(
            base_url=_API_BASE,
            headers={
                "x-api-key": config.api_key,
                "Content-Type": "application/json",
            },
        )

    async def authenticate(self) -> None:
        """No-op — JumpCloud uses a static API key set at initialisation time."""

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all systems from JumpCloud using skip/limit pagination.

        Iterates pages until the cumulative count reaches ``totalCount`` or a
        page returns fewer results than the requested limit.  Systems without a
        serial number are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []
        skip = 0

        while True:
            response = await request_with_retry(
                self._client,
                "GET",
                "/systems",
                params={"limit": _PAGE_LIMIT, "skip": skip},
            )
            response.raise_for_status()
            data = response.json()

            results: list[dict] = data.get("results", [])
            if not results:
                break

            for raw in results:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "JumpCloud system missing serial — skipping",
                        provider=self.name,
                        display_name=raw.get("displayName"),
                        system_id=raw.get("_id"),
                    )

            total_count: int = data.get("totalCount", 0)
            skip += len(results)
            if skip >= total_count or len(results) < _PAGE_LIMIT:
                break

        log.info("JumpCloud systems fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from agent status and disk encryption state.

        A device is compliant when:

        * The agent is ``active``.
        * Full Disk Encryption is either absent (not managed/reported) or
          active (``fde.active == True``).

        Args:
            device: Raw JumpCloud system object from the API.

        Returns:
            ``True`` if the device passes compliance checks.
        """
        if not device.get("active", False):
            return False

        fde = device.get("fde") or {}
        # Only fail if FDE is explicitly reported as inactive
        if fde and fde.get("active") is False:
            return False

        return True

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw JumpCloud system dict to a :class:`ProviderDevice`.

        Args:
            device: Raw system object from the JumpCloud API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("serialNumber") or ""

        last_seen: datetime | None = None
        last_contact = device.get("lastContact")
        if last_contact:
            try:
                last_seen = datetime.fromisoformat(
                    last_contact.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("displayName") or device.get("hostname"),
            os_name=device.get("os"),
            os_version=device.get("osVersion"),
            is_online=device.get("active", False),
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
