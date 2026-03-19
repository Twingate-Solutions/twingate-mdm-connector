"""Tests for WebhookNotifier — HTTP transport mocked throughout."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.config import WebhookConfig
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.notifications.webhook import WebhookNotifier


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _wh_cfg(**overrides) -> WebhookConfig:
    defaults = dict(
        url="https://hooks.example.com/endpoint",
        secret=None,
        events=["device_trusted", "provider_error", "sync_complete"],
        timeout_seconds=10,
    )
    defaults.update(overrides)
    return WebhookConfig.model_validate(defaults)


@pytest.fixture
def trust_event() -> TrustEvent:
    return TrustEvent(
        device_id="dev-1", device_name="CORP-01", serial_number="ABCD1234",
        os_name="Windows", user_email="user@example.com",
        providers=("ninjaone",), timestamp=_now(), dry_run=False,
    )


@pytest.fixture
def error_event() -> ProviderErrorEvent:
    return ProviderErrorEvent(
        provider_name="sophos", error_message="Timeout", timestamp=_now()
    )


@pytest.fixture
def sync_event() -> SyncCompleteEvent:
    return SyncCompleteEvent(
        total_untrusted=5, total_trusted=2, total_skipped=1,
        total_no_match=2, total_errors=0,
        provider_names=("ninjaone",), cycle_number=1, timestamp=_now(),
    )


class TestEventFiltering:
    @pytest.mark.asyncio
    async def test_device_trusted_fires_when_in_events(
        self, trust_event: TrustEvent
    ) -> None:
        notifier = WebhookNotifier(_wh_cfg())
        with patch("src.notifications.webhook.request_with_retry",
                   new_callable=AsyncMock, return_value=AsyncMock(status_code=200)) as mock:
            await notifier.on_device_trusted(trust_event)
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_device_trusted_suppressed_when_not_in_events(
        self, trust_event: TrustEvent
    ) -> None:
        notifier = WebhookNotifier(_wh_cfg(events=["provider_error"]))
        with patch("src.notifications.webhook.request_with_retry",
                   new_callable=AsyncMock) as mock:
            await notifier.on_device_trusted(trust_event)
        mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_error_fires(self, error_event: ProviderErrorEvent) -> None:
        notifier = WebhookNotifier(_wh_cfg())
        with patch("src.notifications.webhook.request_with_retry",
                   new_callable=AsyncMock, return_value=AsyncMock(status_code=200)) as mock:
            await notifier.on_provider_error(error_event)
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_complete_fires(self, sync_event: SyncCompleteEvent) -> None:
        notifier = WebhookNotifier(_wh_cfg())
        with patch("src.notifications.webhook.request_with_retry",
                   new_callable=AsyncMock, return_value=AsyncMock(status_code=200)) as mock:
            await notifier.on_sync_complete(sync_event)
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_is_non_fatal(self, trust_event: TrustEvent) -> None:
        notifier = WebhookNotifier(_wh_cfg())
        with patch("src.notifications.webhook.request_with_retry",
                   new_callable=AsyncMock, side_effect=Exception("refused")):
            await notifier.on_device_trusted(trust_event)  # must not raise


class TestHmacSigning:
    @pytest.mark.asyncio
    async def test_signature_header_present_when_secret_set(
        self, trust_event: TrustEvent
    ) -> None:
        notifier = WebhookNotifier(_wh_cfg(secret="mysecret"))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(kwargs)
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        assert "X-Hub-Signature-256" in captured[0]["headers"]

    @pytest.mark.asyncio
    async def test_signature_is_correct(self, trust_event: TrustEvent) -> None:
        secret = "supersecret"
        notifier = WebhookNotifier(_wh_cfg(secret=secret))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(kwargs)
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        body = captured[0]["content"]
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert captured[0]["headers"]["X-Hub-Signature-256"] == expected

    @pytest.mark.asyncio
    async def test_no_signature_header_without_secret(
        self, trust_event: TrustEvent
    ) -> None:
        notifier = WebhookNotifier(_wh_cfg(secret=None))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(kwargs)
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        assert "X-Hub-Signature-256" not in captured[0].get("headers", {})


class TestPayloadSchema:
    @pytest.mark.asyncio
    async def test_trust_event_payload(self, trust_event: TrustEvent) -> None:
        notifier = WebhookNotifier(_wh_cfg())
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(json.loads(kwargs["content"]))
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        p = captured[0]
        assert p["event"] == "device_trusted"
        assert "timestamp" in p
        assert p["device"]["serial_masked"] == "****1234"
        assert p["device"]["hostname"] == "CORP-01"
        assert p["device"]["user_email"] == "user@example.com"
        assert p["result"]["trusted"] is True
        assert p["result"]["dry_run"] is False
