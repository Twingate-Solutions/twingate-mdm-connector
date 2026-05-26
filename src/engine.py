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
from datetime import datetime, timezone
from typing import Literal

from src.config import AppConfig
from src.matching import build_provider_index, evaluate_trust, normalize_serial
from src.notifications.base import NullNotifier, Notifier, ProviderErrorEvent, SyncCompleteEvent, TrustEvent
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


@dataclass
class _DeviceDecision:
    """Outcome of evaluating a single Twingate device.

    ``kind`` is one of:
      - ``"trustable"`` — device should be trusted; ``contributors`` lists the
        provider names that voted yes.
      - ``"skipped"`` — device matched somewhere but did not pass trust checks.
      - ``"no_match"`` — device was not claimed by any provider or evaluator.
    """

    kind: Literal["trustable", "skipped", "no_match"]
    contributors: list[str]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


async def run_sync_cycle(
    config: AppConfig,
    providers: list[ProviderPlugin],
    tg_client: TwingateClient,
    notifier: Notifier | None = None,
) -> CycleSummary:
    """Execute one full sync cycle.

    Args:
        config: Validated application config.
        providers: Instantiated, enabled provider plugins.
        tg_client: An open :class:`~src.twingate.client.TwingateClient`
            (caller is responsible for lifecycle).
        notifier: Notification channel. Defaults to a no-op :class:`NullNotifier`.

    Returns:
        A :class:`CycleSummary` with per-provider and aggregate stats.
    """
    if notifier is None:
        notifier = NullNotifier()
    summary = CycleSummary()

    # Split providers into archetypes.  An evaluator provider is any provider
    # whose subclass overrides `evaluate_device`; the rest are inventory.
    inventory_providers: list[ProviderPlugin] = []
    evaluator_providers: list[ProviderPlugin] = []
    for p in providers:
        cls_eval = getattr(type(p), "evaluate_device", None)
        if cls_eval is not None and cls_eval is not ProviderPlugin.evaluate_device:
            evaluator_providers.append(p)
        else:
            inventory_providers.append(p)

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
            await notifier.on_provider_error(ProviderErrorEvent(
                provider_name=plugin.name,
                error_message=str(exc),
                timestamp=datetime.now(tz=timezone.utc),
            ))

    await asyncio.gather(*[_fetch_provider(p) for p in inventory_providers])

    # Register evaluator providers in the stats map as always-available with
    # no inventory fetch.
    for p in evaluator_providers:
        provider_stats_map[p.name] = ProviderStats(
            name=p.name, devices_fetched=0, available=True
        )

    summary.provider_stats = list(provider_stats_map.values())

    available_inventory = [
        p for p in inventory_providers if provider_stats_map[p.name].available
    ]
    available_evaluators = list(evaluator_providers)

    if not available_inventory and not available_evaluators:
        logger.warning("No providers available for this cycle — skipping trust evaluation")
        _log_summary(summary)
        await notifier.on_sync_complete(SyncCompleteEvent(
            total_untrusted=summary.total_untrusted,
            total_trusted=summary.total_trusted,
            total_skipped=summary.total_skipped,
            total_no_match=summary.total_no_match,
            total_errors=summary.total_errors,
            provider_names=tuple(p.name for p in providers),
            cycle_number=0,
            timestamp=datetime.now(tz=timezone.utc),
        ))
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
        _log_summary(summary)
        await notifier.on_sync_complete(SyncCompleteEvent(
            total_untrusted=summary.total_untrusted,
            total_trusted=summary.total_trusted,
            total_skipped=summary.total_skipped,
            total_no_match=summary.total_no_match,
            total_errors=summary.total_errors,
            provider_names=tuple(p.name for p in providers),
            cycle_number=0,
            timestamp=datetime.now(tz=timezone.utc),
        ))
        return summary

    summary.total_untrusted = len(untrusted)
    logger.info("Fetched untrusted Twingate devices", count=summary.total_untrusted)

    # ------------------------------------------------------------------ #
    # Step 3: Match + evaluate each device → collect trustable decisions  #
    # ------------------------------------------------------------------ #
    trustable: list[tuple[TwingateDevice, list[str]]] = []
    for tg_device in untrusted:
        decision = await _process_device(
            tg_device=tg_device,
            config=config,
            available_inventory=available_inventory,
            available_evaluators=available_evaluators,
            provider_indices=provider_indices,
            provider_stats_map=provider_stats_map,
            summary=summary,
        )
        if decision.kind == "trustable":
            trustable.append((tg_device, decision.contributors))

    # ------------------------------------------------------------------ #
    # Step 4: Fan-out trust mutations with a configured concurrency cap   #
    # ------------------------------------------------------------------ #
    await _trust_devices_batched(
        trustable=trustable,
        config=config,
        tg_client=tg_client,
        summary=summary,
        notifier=notifier,
    )

    # ------------------------------------------------------------------ #
    # Step 5: Log summary                                                 #
    # ------------------------------------------------------------------ #
    _log_summary(summary)
    await notifier.on_sync_complete(SyncCompleteEvent(
        total_untrusted=summary.total_untrusted,
        total_trusted=summary.total_trusted,
        total_skipped=summary.total_skipped,
        total_no_match=summary.total_no_match,
        total_errors=summary.total_errors,
        provider_names=tuple(p.name for p in providers),
        cycle_number=0,
        timestamp=datetime.now(tz=timezone.utc),
    ))
    return summary


async def _process_device(
    tg_device: TwingateDevice,
    config: AppConfig,
    available_inventory: list[ProviderPlugin],
    available_evaluators: list[ProviderPlugin],
    provider_indices: dict[str, dict[str, ProviderDevice]],
    provider_stats_map: dict[str, ProviderStats],
    summary: CycleSummary,
) -> _DeviceDecision:
    """Evaluate a single untrusted Twingate device and return its decision.

    This function no longer issues the trust mutation directly — callers
    collect ``"trustable"`` decisions and dispatch them in a batched fan-out
    via :func:`_trust_devices_batched`.
    """
    serial = normalize_serial(tg_device.serial_number)

    # Build per-provider lookup results for this device.
    provider_results: dict[str, ProviderDevice | None] = {}

    if serial is not None:
        for plugin in available_inventory:
            index = provider_indices.get(plugin.name, {})
            match = index.get(serial)
            provider_results[plugin.name] = match
            if match:
                provider_stats_map[plugin.name].matches_found += 1
    else:
        # No serial — inventory providers cannot match.  Record explicit None
        # for each so trust.mode=all sees a vote-no rather than an absent provider.
        for plugin in available_inventory:
            provider_results[plugin.name] = None

    # Evaluator providers are called per-device regardless of serial presence.
    # Per the project's "provider failure is non-fatal" rule, an exception
    # from one evaluator is logged and treated as a no-claim — it must not
    # abort processing for this device.
    for evaluator in available_evaluators:
        try:
            result = await evaluator.evaluate_device(tg_device)
        except Exception as exc:
            logger.error(
                "Evaluator provider raised — treating as no-claim for this device",
                provider=evaluator.name,
                twingate_device_id=tg_device.id,
                error=str(exc),
            )
            provider_stats_map[evaluator.name].errors += 1
            provider_results[evaluator.name] = None
            continue
        provider_results[evaluator.name] = result
        if result is not None:
            provider_stats_map[evaluator.name].matches_found += 1

    matched_any = any(v is not None for v in provider_results.values())
    if not matched_any:
        logger.debug(
            "NO MATCH: device not found in any provider or evaluator",
            twingate_device_id=tg_device.id,
            device_serial=serial,
            device_name=tg_device.name,
        )
        summary.total_no_match += 1
        return _DeviceDecision(kind="no_match", contributors=[])

    should_trust, contributors = evaluate_trust(
        tg_device=tg_device,
        provider_results=provider_results,
        mode=config.trust.mode,
        require_online=config.trust.require_online,
        require_compliant=config.trust.require_compliant,
        max_days_since_checkin=config.trust.max_days_since_checkin,
    )

    summary.total_matched += 1

    if should_trust:
        return _DeviceDecision(kind="trustable", contributors=contributors)

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
    return _DeviceDecision(kind="skipped", contributors=[])


async def _trust_device(
    tg_device: TwingateDevice,
    serial: str,
    contributors: list[str],
    config: AppConfig,
    tg_client: TwingateClient,
    summary: CycleSummary,
    notifier: Notifier | None = None,
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
        await notifier.on_device_trusted(TrustEvent(
            device_id=tg_device.id,
            device_name=tg_device.name,
            serial_number=serial,
            os_name=tg_device.os_name,
            user_email=tg_device.user.email if tg_device.user else None,
            providers=tuple(contributors),
            timestamp=datetime.now(tz=timezone.utc),
            dry_run=True,
        ))
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
            await notifier.on_device_trusted(TrustEvent(
                device_id=tg_device.id,
                device_name=tg_device.name,
                serial_number=serial,
                os_name=tg_device.os_name,
                user_email=tg_device.user.email if tg_device.user else None,
                providers=tuple(contributors),
                timestamp=datetime.now(tz=timezone.utc),
                dry_run=False,
            ))
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


async def _trust_devices_batched(
    trustable: list[tuple[TwingateDevice, list[str]]],
    config: AppConfig,
    tg_client: TwingateClient,
    summary: CycleSummary,
    notifier: Notifier,
) -> None:
    """Issue trust mutations concurrently with a configured fan-out cap.

    Reuses :data:`config.sync.batch_size` as the maximum number of in-flight
    mutations.  Each individual mutation still emits its own log line and
    ``device_trusted`` event — only the dispatch is batched.

    Per-device failures are caught inside :func:`_trust_device` and do not
    abort the batch.
    """
    if not trustable:
        return

    semaphore = asyncio.Semaphore(config.sync.batch_size)

    async def _one(tg_device: TwingateDevice, contributors: list[str]) -> None:
        async with semaphore:
            serial = normalize_serial(tg_device.serial_number) or ""
            await _trust_device(
                tg_device=tg_device,
                serial=serial,
                contributors=contributors,
                config=config,
                tg_client=tg_client,
                summary=summary,
                notifier=notifier,
            )

    await asyncio.gather(*[_one(d, c) for d, c in trustable])


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
