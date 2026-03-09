"""FleetDM provider plugin.

Auth:  API token as Bearer token — no OAuth flow required.
API:   https://{fleet-server}/api/v1/fleet
Docs:  https://fleetdm.com/docs/rest-api/rest-api

List hosts:   GET /api/v1/fleet/hosts  (page, per_page; meta.has_next_results)
Host detail:  GET /api/v1/fleet/hosts/{id}  (includes policies array)

Compliance:   All policies must have response == "pass".  An empty policy list
              is treated as compliant (no policies configured).
              Detail calls are fetched concurrently (semaphore-bounded).
"""

import asyncio
from datetime import UTC, datetime

import structlog

from src.config import FleetDMConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.utils.http import build_client, request_with_retry

log = structlog.get_logger()

_PER_PAGE = 500
_DETAIL_CONCURRENCY = 20  # max simultaneous host-detail calls


class FleetDMProvider(ProviderPlugin):
    """FleetDM provider plugin.

    Fetches all hosts from a self-hosted FleetDM instance.  Compliance is
    evaluated by checking every configured osquery policy via the per-host
    detail endpoint.  Detail calls are batched with a semaphore to avoid
    overwhelming the Fleet server.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in log output."""
        return "fleetdm"

    def __init__(self, config: FleetDMConfig) -> None:
        """Initialise the FleetDM provider.

        Args:
            config: FleetDM configuration from YAML / env.  ``url`` must be
                the base URL of the Fleet server including scheme but without
                a trailing slash (e.g. ``https://fleet.company.com``).
        """
        self._config = config
        base_url = config.url.rstrip("/")
        self._client = build_client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {config.api_token}"},
        )

    async def authenticate(self) -> None:
        """No-op — FleetDM uses a static API token set at initialisation time."""

    async def list_devices(self) -> list[ProviderDevice]:
        """Fetch all hosts from FleetDM and evaluate policy compliance.

        Two-phase fetch:

        1. Paginate ``/api/v1/fleet/hosts`` to collect all host summaries.
        2. Fetch ``/api/v1/fleet/hosts/{id}`` for each host in parallel
           (bounded by :attr:`_DETAIL_CONCURRENCY`) to get policy results.

        Hosts without a ``hardware_serial`` are silently skipped.

        Returns:
            List of normalised :class:`~src.providers.base.ProviderDevice`
            instances.

        Raises:
            httpx.HTTPStatusError: On non-retryable list-endpoint errors.
        """
        # Phase 1: collect host summaries
        hosts: list[dict] = []
        page = 1

        while True:
            response = await request_with_retry(
                self._client,
                "GET",
                "/api/v1/fleet/hosts",
                params={"per_page": _PER_PAGE, "page": page},
            )
            response.raise_for_status()
            data = response.json()

            page_hosts: list[dict] = data.get("hosts") or []
            hosts.extend(page_hosts)

            if not (data.get("meta") or {}).get("has_next_results", False):
                break
            page += 1

        if not hosts:
            log.info("FleetDM hosts fetched", provider=self.name, count=0)
            return []

        # Phase 2: fetch detail (policies) for each host concurrently
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def _fetch_detail(host: dict) -> dict:
            async with semaphore:
                try:
                    resp = await request_with_retry(
                        self._client,
                        "GET",
                        f"/api/v1/fleet/hosts/{host['id']}",
                    )
                    resp.raise_for_status()
                    return resp.json().get("host") or host
                except Exception as exc:
                    log.warning(
                        "Failed to fetch FleetDM host detail — using list data",
                        provider=self.name,
                        host_id=host.get("id"),
                        error=str(exc),
                    )
                    return host  # fall back to list data (no policy info)

        details: list[dict] = list(
            await asyncio.gather(*[_fetch_detail(h) for h in hosts])
        )

        devices: list[ProviderDevice] = []
        for detail in details:
            device = self._build_device(detail)
            if device.serial_number:
                devices.append(device)
            else:
                log.debug(
                    "FleetDM host missing serial — skipping",
                    provider=self.name,
                    hostname=detail.get("hostname"),
                    host_id=detail.get("id"),
                )

        log.info("FleetDM hosts fetched", provider=self.name, count=len(devices))
        return devices

    def determine_compliance(self, device: dict) -> bool:
        """Evaluate compliance from osquery policy results.

        All policies must have ``response == "pass"``.  A device with no
        configured policies is considered compliant.

        Args:
            device: Raw FleetDM host detail object from the API.

        Returns:
            ``True`` if every policy passes (or no policies are configured).
        """
        policies: list[dict] = device.get("policies") or []
        if not policies:
            return True
        return all(p.get("response") == "pass" for p in policies)

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw FleetDM host dict to a :class:`ProviderDevice`.

        Args:
            device: Raw host object from the FleetDM API (list or detail form).

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("hardware_serial") or ""

        last_seen: datetime | None = None
        for ts_field in ("last_enrolled_at", "seen_time", "last_restarted_at"):
            raw_ts = device.get(ts_field)
            if raw_ts:
                try:
                    last_seen = datetime.fromisoformat(
                        raw_ts.replace("Z", "+00:00")
                    )
                    break
                except (ValueError, AttributeError):
                    continue

        return ProviderDevice(
            serial_number=serial_raw.strip().upper(),
            hostname=device.get("hostname") or device.get("computer_name"),
            os_name=device.get("platform"),
            os_version=device.get("os_version"),
            is_online=device.get("status") == "online",
            is_compliant=self.determine_compliance(device),
            last_seen=last_seen,
            raw=device,
        )
