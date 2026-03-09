"""Application entrypoint for twingate-device-trust-bridge.

Loads configuration, configures logging, instantiates enabled provider
plugins, and starts the scheduler loop.

Run as a module:

    python -m src.main

Or via Docker:

    CMD ["python", "-m", "src.main"]
"""

import asyncio
import os
import sys

from src.config import AppConfig, load_config
from src.providers.base import ProviderPlugin
from src.scheduler import run_scheduler
from src.utils.logging import configure_logging, get_logger

# Logger is obtained after configure_logging() is called below.
# We declare it here so type checkers are happy; it is reassigned in main().
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _build_providers(config: AppConfig) -> list[ProviderPlugin]:
    """Instantiate enabled provider plugins from the config.

    Imports are deferred to this function so that provider modules are only
    loaded when they are actually enabled.  This keeps startup fast and avoids
    import errors for providers whose dependencies might be missing.

    Args:
        config: Fully validated :class:`~src.config.AppConfig`.

    Returns:
        A list of :class:`~src.providers.base.ProviderPlugin` instances, one
        per enabled provider.
    """
    from src.config import (
        AutomoxConfig,
        DattoConfig,
        FleetDMConfig,
        JumpCloudConfig,
        ManageEngineConfig,
        MosyleConfig,
        NinjaOneConfig,
        SophosConfig,
    )

    plugins: list[ProviderPlugin] = []

    for provider_config in config.enabled_providers:
        match provider_config:
            case NinjaOneConfig():
                from src.providers.ninjaone import NinjaOneProvider
                plugins.append(NinjaOneProvider(provider_config))

            case SophosConfig():
                from src.providers.sophos import SophosProvider
                plugins.append(SophosProvider(provider_config))

            case ManageEngineConfig():
                from src.providers.manageengine import ManageEngineProvider
                plugins.append(ManageEngineProvider(provider_config))

            case AutomoxConfig():
                from src.providers.automox import AutomoxProvider
                plugins.append(AutomoxProvider(provider_config))

            case JumpCloudConfig():
                from src.providers.jumpcloud import JumpCloudProvider
                plugins.append(JumpCloudProvider(provider_config))

            case FleetDMConfig():
                from src.providers.fleetdm import FleetDMProvider
                plugins.append(FleetDMProvider(provider_config))

            case MosyleConfig():
                from src.providers.mosyle import MosyleProvider
                plugins.append(MosyleProvider(provider_config))

            case DattoConfig():
                from src.providers.datto import DattoProvider
                plugins.append(DattoProvider(provider_config))

    return plugins


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _run() -> None:
    """Main coroutine — loads config, builds providers, starts scheduler."""
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")

    # Load config first so we can configure logging at the right level.
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        # Logging isn't configured yet — write directly to stderr.
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"FATAL: config error — {exc}", file=sys.stderr)
        sys.exit(1)

    # Configure logging with the level from config.
    configure_logging(config.logging.level)
    global logger
    logger = get_logger(__name__)

    logger.info(
        "twingate-device-trust-bridge starting",
        config_path=config_path,
        dry_run=config.sync.dry_run,
        trust_mode=config.trust.mode,
        enabled_providers=[p.type for p in config.enabled_providers],
    )

    if not config.enabled_providers:
        logger.error("No providers are enabled — nothing to do. Check your config.yaml.")
        sys.exit(1)

    providers = _build_providers(config)
    logger.info("Providers instantiated", count=len(providers), names=[p.name for p in providers])

    await run_scheduler(config, providers)


def main() -> None:
    """Synchronous wrapper — entry point for ``python -m src.main``."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
