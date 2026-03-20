"""Tests for WebhookNotifier — HTTP transport mocked throughout."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.config import NotificationsConfig, WebhookConfig
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
        format="raw",
        headers=None,
        templates_dir=None,
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


class TestWebhookTemplates:
    @pytest.mark.asyncio
    async def test_slack_format_sends_slack_payload(self, trust_event: TrustEvent) -> None:
        """Slack format renders a slack-shaped payload."""
        notifier = WebhookNotifier(_wh_cfg(format="slack"))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(json.loads(kwargs["content"]))
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        assert "text" in captured[0]
        assert "CORP-01" in captured[0]["text"]

    @pytest.mark.asyncio
    async def test_teams_format_sends_message_card(self, trust_event: TrustEvent) -> None:
        """Teams format renders a MessageCard payload."""
        notifier = WebhookNotifier(_wh_cfg(format="teams"))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(json.loads(kwargs["content"]))
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        assert captured[0]["@type"] == "MessageCard"
        assert "CORP-01" in captured[0]["summary"]

    @pytest.mark.asyncio
    async def test_discord_format_sends_embeds(self, sync_event: SyncCompleteEvent) -> None:
        """Discord format renders an embeds payload."""
        notifier = WebhookNotifier(_wh_cfg(format="discord"))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(json.loads(kwargs["content"]))
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_sync_complete(sync_event)

        assert "embeds" in captured[0]

    @pytest.mark.asyncio
    async def test_custom_headers_sent(self, trust_event: TrustEvent) -> None:
        """Custom static headers are included in the request."""
        notifier = WebhookNotifier(_wh_cfg(headers={"Authorization": "GenieKey abc123"}))
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(kwargs)
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        assert captured[0]["headers"]["Authorization"] == "GenieKey abc123"

    @pytest.mark.asyncio
    async def test_custom_headers_with_hmac(self, trust_event: TrustEvent) -> None:
        """Custom headers coexist with HMAC signing."""
        notifier = WebhookNotifier(
            _wh_cfg(secret="s3cret", headers={"X-Custom": "value"})
        )
        captured: list[dict] = []

        async def capture(client, method, url, **kwargs):
            captured.append(kwargs)
            return AsyncMock(status_code=200)

        with patch("src.notifications.webhook.request_with_retry", side_effect=capture):
            await notifier.on_device_trusted(trust_event)

        headers = captured[0]["headers"]
        assert headers["X-Custom"] == "value"
        assert "X-Hub-Signature-256" in headers

    def test_load_webhook_template_bundled(self) -> None:
        """Bundled slack template loads successfully."""
        from src.notifications.webhook import load_webhook_template
        text = load_webhook_template("slack", "device_trusted", None)
        assert "$device_hostname" in text

    def test_load_webhook_template_unknown_format_raises(self) -> None:
        """Unknown format raises FileNotFoundError."""
        from src.notifications.webhook import load_webhook_template
        with pytest.raises(FileNotFoundError):
            load_webhook_template("nonexistent_format", "device_trusted", None)

    def test_load_webhook_template_custom_dir_takes_priority(self, tmp_path) -> None:
        """User templates_dir overrides bundled templates."""
        from src.notifications.webhook import load_webhook_template
        custom = tmp_path / "slack_device_trusted.json"
        custom.write_text('{"text": "custom $device_hostname"}', encoding="utf-8")
        text = load_webhook_template("slack", "device_trusted", str(tmp_path))
        assert "custom" in text


class TestNotificationsConfig:
    def test_webhooks_is_list(self) -> None:
        """webhooks field accepts a list of WebhookConfig entries."""
        cfg = NotificationsConfig.model_validate({
            "webhooks": [
                {"url": "https://a.example.com", "format": "slack"},
                {"url": "https://b.example.com", "format": "teams"},
            ]
        })
        assert len(cfg.webhooks) == 2
        assert cfg.webhooks[0].format == "slack"
        assert cfg.webhooks[1].format == "teams"

    def test_webhooks_defaults_to_empty_list(self) -> None:
        """webhooks defaults to an empty list when omitted."""
        cfg = NotificationsConfig.model_validate({})
        assert cfg.webhooks == []

    def test_webhook_config_defaults(self) -> None:
        """New WebhookConfig fields have sensible defaults."""
        cfg = WebhookConfig.model_validate({"url": "https://example.com"})
        assert cfg.format == "raw"
        assert cfg.headers is None
        assert cfg.templates_dir is None
