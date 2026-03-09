"""Async scheduler — runs the sync engine on a fixed interval.

The scheduler is a simple ``asyncio`` loop:

1. Run a sync cycle.
2. Sleep for ``config.sync.interval_seconds``.
3. Repeat until cancelled.

Cancellation (e.g. via ``SIGTERM``) is handled gracefully by catching
:class:`asyncio.CancelledError` and logging a clean shutdown message.
"""

import asyncio

from src.config import AppConfig
from src.engine import run_sync_cycle
from src.providers.base import ProviderPlugin
from src.twingate.client import TwingateClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def run_scheduler(
    config: AppConfig,
    providers: list[ProviderPlugin],
) -> None:
    """Start the sync scheduler loop.

    Runs indefinitely until the coroutine is cancelled.  Each iteration:

    1. Opens a fresh :class:`~src.twingate.client.TwingateClient` context.
    2. Calls :func:`~src.engine.run_sync_cycle`.
    3. Sleeps for ``config.sync.interval_seconds``.

    Args:
        config: Validated application configuration.
        providers: Instantiated, enabled :class:`~src.providers.base.ProviderPlugin`
            instances (already constructed — this function does not create them).
    """
    interval = config.sync.interval_seconds
    dry_run = config.sync.dry_run

    logger.info(
        "Scheduler started",
        interval_seconds=interval,
        dry_run=dry_run,
        providers=[p.name for p in providers],
    )

    cycle = 0
    try:
        while True:
            cycle += 1
            logger.info("Starting sync cycle", cycle=cycle)

            try:
                async with TwingateClient(
                    tenant=config.twingate.tenant,
                    api_key=config.twingate.api_key,
                    batch_size=config.sync.batch_size,
                ) as tg_client:
                    await run_sync_cycle(
                        config=config,
                        providers=providers,
                        tg_client=tg_client,
                    )
            except Exception as exc:
                # Cycle-level error (e.g. Twingate API totally unavailable).
                # Log and continue — the scheduler keeps running.
                logger.error(
                    "Sync cycle failed with unhandled exception",
                    cycle=cycle,
                    error=str(exc),
                    exc_info=True,
                )

            logger.info("Sleeping until next cycle", seconds=interval)
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("Scheduler cancelled — shutting down cleanly")
        raise
