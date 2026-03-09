"""Shared pytest fixtures for the twingate-device-trust-bridge test suite."""

import pytest

from src.config import (
    AppConfig,
    LoggingConfig,
    MatchingConfig,
    SyncConfig,
    TrustConfig,
    TwingateConfig,
)


@pytest.fixture
def minimal_config() -> AppConfig:
    """A minimal valid AppConfig with no providers enabled."""
    return AppConfig(
        twingate=TwingateConfig(tenant="test-tenant", api_key="test-api-key"),
        sync=SyncConfig(interval_seconds=60, dry_run=True, batch_size=10),
        matching=MatchingConfig(),
        trust=TrustConfig(),
        providers=[],
        logging=LoggingConfig(),
    )
