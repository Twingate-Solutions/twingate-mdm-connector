"""FleetDM provider plugin.

Auth:  API token as Bearer token — no OAuth flow required.
API:   https://{fleet-server}/api/v1/fleet
Docs:  https://fleetdm.com/docs/rest-api/rest-api

List hosts:   GET /api/v1/fleet/hosts  (page is 0-indexed; paginate until a
              page returns fewer than per_page rows)
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
_MAX_PAGES = 500
# The List hosts endpoint does not return a has_next_results flag, so we infer
# the last page from its size.  A page returning fewer than (per_page - buffer)
# rows is treated as the final one; the buffer errs toward one extra (possibly
# empty) request rather than risking a silent truncation.
_PAGE_BUFFER = 10


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
        self._client = build_client(base_url=base_url)

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
        # Phase 1: collect host summaries.
        # FleetDM pagination is 0-indexed: the first page is page 0.  Starting
        # at page 1 skips the entire first page, which for any fleet that fits
        # in a single page returns zero hosts (see issue #7).
        hosts: list[dict] = []
        page = 0
        auth_headers = {"Authorization": f"Bearer {self._config.api_token}"}

        for _page_num in range(_MAX_PAGES):
            response = await request_with_retry(
                self._client,
                "GET",
                "/api/v1/fleet/hosts",
                headers=auth_headers,
                params={"per_page": _PER_PAGE, "page": page},
            )
            response.raise_for_status()
            data = response.json()

            page_hosts: list[dict] = data.get("hosts") or []
            hosts.extend(page_hosts)

            # A page returning fewer than per_page rows (allowing a small
            # buffer) is the last one — see _PAGE_BUFFER.
            if len(page_hosts) < _PER_PAGE - _PAGE_BUFFER:
                break
            page += 1
        else:
            log.warning(
                "FleetDM pagination safety limit reached — results may be incomplete",
                provider=self.name,
                max_pages=_MAX_PAGES,
            )

        if not hosts:
            log.info("FleetDM hosts fetched", provider=self.name, count=0)
            return []

        # Phase 2: fetch detail (policies) for each host concurrently
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def _fetch_detail(host: dict) -> tuple[dict, bool]:
            async with semaphore:
                try:
                    resp = await request_with_retry(
                        self._client,
                        "GET",
                        f"/api/v1/fleet/hosts/{host['id']}",
                        headers=auth_headers,
                    )
                    resp.raise_for_status()
                    return resp.json().get("host") or host, True
                except Exception as exc:
                    log.warning(
                        "Failed to fetch FleetDM host detail — using list data, "
                        "treating as non-compliant",
                        provider=self.name,
                        host_id=host.get("id"),
                        error=str(exc),
                    )
                    return host, False  # detail (incl. policies) unavailable

        details: list[tuple[dict, bool]] = list(
            await asyncio.gather(*[_fetch_detail(h) for h in hosts])
        )

        devices: list[ProviderDevice] = []
        for detail, detail_ok in details:
            device = self._build_device(detail)
            if not detail_ok:
                # Compliance is unknown when the detail call failed (no policy
                # data).  Fail closed so require_compliant does not trust a
                # device on incomplete information.
                device.is_compliant = False
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

        A device is compliant when no policy has an explicit failing result.
        A policy ``response`` of ``""`` means the policy has not been evaluated
        yet (e.g. a freshly enrolled host) and is treated as "no verdict" rather
        than a failure — only a non-empty, non-``"pass"`` response (typically
        ``"fail"``) marks the device non-compliant.  A device with no configured
        policies is considered compliant (there is nothing to fail).

        Args:
            device: Raw FleetDM host detail object from the API.

        Returns:
            ``True`` unless a policy has an explicit failing response.
        """
        policies: list[dict] = device.get("policies") or []
        if not policies:
            return True
        return all(p.get("response") in ("pass", "") for p in policies)

    def _build_device(self, device: dict) -> ProviderDevice:
        """Convert a raw FleetDM host dict to a :class:`ProviderDevice`.

        Args:
            device: Raw host object from the FleetDM API (list or detail form).

        Returns:
            Normalised :class:`ProviderDevice`.
        """
        serial_raw: str = device.get("hardware_serial") or ""

        # Order matters: seen_time is Fleet's check-in heartbeat ("the last
        # time the host contacted the fleet server"), which is what
        # max_days_since_checkin is meant to measure.  last_enrolled_at is a
        # one-time enrollment timestamp and last_restarted_at is a reboot time
        # — both are poor recency signals, so they are fallbacks only (issue #7).
        last_seen: datetime | None = None
        for ts_field in ("seen_time", "last_restarted_at", "last_enrolled_at"):
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
