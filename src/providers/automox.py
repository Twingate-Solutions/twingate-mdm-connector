"""Automox provider plugin.

Auth:  API key as Bearer token — no OAuth flow required.
API:   https://console.automox.com/api
Docs:  https://developer.automox.com/openapi/axconsole/operation/getDevices/

Pagination: 0-indexed ``page`` + ``limit`` (max 500). Loop until a page
            returns fewer results than the requested limit.

Compliance: ``is_compatible`` flag + ``pending_patches == 0``.
"""

from datetime import UTC, datetime

import structlog

from src.config import AutomoxConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import build_client, request_with_retry

log = structlog.get_logger()

_API_BASE = "https://console.automox.com/api"
_PAGE_LIMIT = 500


class AutomoxProvider(ProviderPlugin):
    """Automox provider plugin.

    Fetches all servers from the Automox REST API using a static API key.
    Compliance is determined by the ``is_compatible`` flag and the count of
    ``pending_patches`` reported on each server object.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "automox"

    def __init__(self, config: AutomoxConfig) -> None:
        """Initialise the Automox provider.

        The API key is injected as a default ``Authorization`` header so every
        request is authenticated without extra per-request configuration.

        Args:
            config: Automox configuration from YAML / env.
        """
        self._config = config
        self._client = build_client(
            base_url=_API_BASE,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    async def authenticate(self) -> None:
        """No-op — Automox uses a static API key set at initialisation time."""

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all servers from Automox using page-based pagination.

        Iterates pages (0-indexed) until a page returns fewer results than
        ``_PAGE_LIMIT``, indicating the last page has been reached.  Servers
        without a serial number are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable API errors.
        """
        devices: list[ProviderDevice] = []
        page = 0

        while True:
            response = await request_with_retry(
                self._client,
                "GET",
                "/servers",
                params={
                    "o": self._config.org_id,
                    "page": page,
                    "limit": _PAGE_LIMIT,
                },
            )
            response.raise_for_status()

            results: list[dict] = response.json()
            if not results:
                break

            for raw in results:
                device = self._build_device(raw)
                if device.serial_number:
                    devices.append(device)
                else:
                    log.debug(
                        "Automox server missing serial — skipping",
                        provider=self.name,
                        server_name=raw.get("name"),
                        server_id=raw.get("id"),
                    )

            if len(results) < _PAGE_LIMIT:
                break
            page += 1

        log.info("Automox servers fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from patch status and device compatibility.

        A device is compliant when:

        * ``is_compatible`` is ``True``.
        * ``pending_patches`` count is zero.

        Args:
            device: Raw Automox server object from the API.

        Returns:
            ``True`` if the device is compatible and fully patched.
        """
        if not device.get("is_compatible", False):
            return False
        if device.get("pending_patches", 0) > 0:
            return False
        return True

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw Automox server dict to a :class:`ProviderDevice`.

        Online status is derived from ``status.agent_status``.  ``last_seen``
        is populated from ``last_disconnect_time`` when available.

        Args:
            device: Raw server object from the Automox API.

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("serial_number") or ""

        last_seen: datetime | None = None
        last_disconnect = device.get("last_disconnect_time")
        if last_disconnect:
            try:
                last_seen = datetime.fromisoformat(
                    last_disconnect.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        # Derive online status from agent_status inside the status object
        status = device.get("status") or {}
        if isinstance(status, dict):
            is_online = status.get("agent_status") == "connected"
        else:
            is_online = False

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("name") or device.get("hostname"),
            os_name=device.get("os_family") or device.get("os_name"),
            os_version=device.get("os_version"),
            is_online=is_online,
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
