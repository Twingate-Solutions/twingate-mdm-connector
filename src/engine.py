"""Sync engine — the core of twingate-device-trust-bridge.

Each call to :func:`run_sync_cycle` performs a full reconciliation:

1. Query all enabled providers **in parallel** for their device inventories.
2. Fetch all untrusted devices from Twingate (exhaustive pagination).
3. For each untrusted device, look it up in every provider's index by serial
   number and evaluate the trust decision (ANY / ALL mode).
4. Trust matching devices via the Twingate ``deviceUpdate`` mutation (or just
   log them in DRY_RUN mode).
5. Log a structured summary at the end of the cycle.
"""

import asyncio
from dataclasses import dataclass, field

from src.config import AppConfig
from src.matching import build_provider_index, evaluate_trust, normalize_serial
from src.providers.base import ProviderDevice, ProviderPlugin
from src.twingate.client import TwingateClient
from src.twingate.models import TwingateDevice
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-cycle stats
# ---------------------------------------------------------------------------


@dataclass
class ProviderStats:
    """Counters for a single provider in one sync cycle."""

    name: str
    devices_fetched: int = 0
    matches_found: int = 0
    errors: int = 0
    available: bool = True


@dataclass
class CycleSummary:
    """Aggregate stats for a completed sync cycle."""

    total_untrusted: int = 0
    total_matched: int = 0
    total_trusted: int = 0
    total_skipped: int = 0
    total_no_match: int = 0
    total_errors: int = 0
    provider_stats: list[ProviderStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


async def run_sync_cycle(
    config: AppConfig,
    providers: list[ProviderPlugin],
    tg_client: TwingateClient,
) -> CycleSummary:
    """Execute one full sync cycle.

    Args:
        config: Validated application config.
        providers: Instantiated, enabled provider plugins.
        tg_client: An open :class:`~src.twingate.client.TwingateClient`
            (caller is responsible for lifecycle).

    Returns:
        A :class:`CycleSummary` with per-provider and aggregate stats.
    """
    summary = CycleSummary()

    # ------------------------------------------------------------------ #
    # Step 1: Fetch devices from all providers in parallel                #
    # ------------------------------------------------------------------ #
    provider_indices: dict[str, dict[str, ProviderDevice]] = {}
    provider_stats_map: dict[str, ProviderStats] = {}

    async def _fetch_provider(plugin: ProviderPlugin) -> None:
        stats = ProviderStats(name=plugin.name)
        provider_stats_map[plugin.name] = stats
        try:
            devices = await plugin.fetch()
            stats.devices_fetched = len(devices)
            provider_indices[plugin.name] = build_provider_index(devices)
            logger.info(
                "Provider fetch complete",
                provider=plugin.name,
                devices_fetched=stats.devices_fetched,
            )
        except Exception as exc:
            stats.available = False
            stats.errors += 1
            logger.error(
                "Provider fetch failed — skipping for this cycle",
                provider=plugin.name,
                error=str(exc),
            )

    await asyncio.gather(*[_fetch_provider(p) for p in providers])
    summary.provider_stats = list(provider_stats_map.values())

    available_providers = [
        p for p in providers if provider_stats_map[p.name].available
    ]

    if not available_providers:
        logger.warning("No providers available for this cycle — skipping trust evaluation")
        return summary

    # ------------------------------------------------------------------ #
    # Step 2: Fetch untrusted devices from Twingate                       #
    # ------------------------------------------------------------------ #
    try:
        untrusted = await tg_client.list_untrusted_devices()
    except Exception as exc:
        logger.error(
            "Failed to fetch untrusted devices from Twingate — aborting cycle",
            error=str(exc),
        )
        return summary

    summary.total_untrusted = len(untrusted)
    logger.info("Fetched untrusted Twingate devices", count=summary.total_untrusted)

    # ------------------------------------------------------------------ #
    # Step 3 + 4: Match, evaluate, and trust each device                  #
    # ------------------------------------------------------------------ #
    for tg_device in untrusted:
        await _process_device(
            tg_device=tg_device,
            config=config,
            available_providers=available_providers,
            provider_indices=provider_indices,
            provider_stats_map=provider_stats_map,
            tg_client=tg_client,
            summary=summary,
        )

    # ------------------------------------------------------------------ #
    # Step 5: Log summary                                                 #
    # ------------------------------------------------------------------ #
    _log_summary(summary)
    return summary


async def _process_device(
    tg_device: TwingateDevice,
    config: AppConfig,
    available_providers: list[ProviderPlugin],
    provider_indices: dict[str, dict[str, ProviderDevice]],
    provider_stats_map: dict[str, ProviderStats],
    tg_client: TwingateClient,
    summary: CycleSummary,
) -> None:
    """Evaluate and potentially trust a single untrusted Twingate device."""
    serial = normalize_serial(tg_device.serial_number)

    if serial is None:
        logger.debug(
            "Twingate device has no serial number — skipping",
            twingate_device_id=tg_device.id,
            device_name=tg_device.name,
        )
        summary.total_no_match += 1
        return

    # Build per-provider lookup results for this device
    provider_results: dict[str, ProviderDevice | None] = {}
    for plugin in available_providers:
        index = provider_indices.get(plugin.name, {})
        match = index.get(serial)
        provider_results[plugin.name] = match
        if match:
            provider_stats_map[plugin.name].matches_found += 1

    # Evaluate trust decision
    should_trust, contributors = evaluate_trust(
        tg_device=tg_device,
        provider_results=provider_results,
        mode=config.trust.mode,
        require_online=config.trust.require_online,
        require_compliant=config.trust.require_compliant,
        max_days_since_checkin=config.trust.max_days_since_checkin,
    )

    matched_any = any(v is not None for v in provider_results.values())

    if not matched_any:
        logger.debug(
            "NO MATCH: device not found in any provider",
            twingate_device_id=tg_device.id,
            device_serial=serial,
            device_name=tg_device.name,
        )
        summary.total_no_match += 1
        return

    summary.total_matched += 1

    if should_trust:
        await _trust_device(
            tg_device=tg_device,
            serial=serial,
            contributors=contributors,
            config=config,
            tg_client=tg_client,
            summary=summary,
        )
    else:
        logger.info(
            "SKIPPED: device found but did not pass trust checks",
            twingate_device_id=tg_device.id,
            device_serial=serial,
            device_name=tg_device.name,
            provider_results={
                k: {
                    "found": v is not None,
                    "online": v.is_online if v else None,
                    "compliant": v.is_compliant if v else None,
                }
                for k, v in provider_results.items()
            },
        )
        summary.total_skipped += 1


async def _trust_device(
    tg_device: TwingateDevice,
    serial: str,
    contributors: list[str],
    config: AppConfig,
    tg_client: TwingateClient,
    summary: CycleSummary,
) -> None:
    """Issue (or simulate) a trust mutation for a device."""
    if config.sync.dry_run:
        logger.info(
            "DRY RUN — WOULD TRUST device",
            twingate_device_id=tg_device.id,
            device_serial=serial,
            device_name=tg_device.name,
            via_providers=contributors,
        )
        summary.total_trusted += 1
        return

    try:
        result = await tg_client.trust_device(tg_device.id)
        if result.ok:
            logger.info(
                "TRUSTED device",
                twingate_device_id=tg_device.id,
                device_serial=serial,
                device_name=tg_device.name,
                via_providers=contributors,
            )
            summary.total_trusted += 1
        else:
            logger.error(
                "Trust mutation failed",
                twingate_device_id=tg_device.id,
                device_serial=serial,
                error=result.error,
            )
            summary.total_errors += 1
    except Exception as exc:
        logger.error(
            "Exception while trusting device",
            twingate_device_id=tg_device.id,
            device_serial=serial,
            error=str(exc),
        )
        summary.total_errors += 1


def _log_summary(summary: CycleSummary) -> None:
    """Emit a structured INFO log with the cycle summary."""
    provider_summary = [
        {
            "provider": s.name,
            "devices_fetched": s.devices_fetched,
            "matches_found": s.matches_found,
            "available": s.available,
            "errors": s.errors,
        }
        for s in summary.provider_stats
    ]
    logger.info(
        "Sync cycle complete",
        total_untrusted=summary.total_untrusted,
        total_matched=summary.total_matched,
        total_trusted=summary.total_trusted,
        total_skipped=summary.total_skipped,
        total_no_match=summary.total_no_match,
        total_errors=summary.total_errors,
        providers=provider_summary,
    )
