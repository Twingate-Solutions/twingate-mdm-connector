"""Tests for NotificationsConfig Pydantic models."""

import pytest
from pydantic import ValidationError

from src.config import (
    AppConfig,
    NotificationsConfig,
    SmtpAlertsConfig,
    SmtpConfig,
    TwingateConfig,
    WebhookConfig,
)


class TestSmtpAlertsConfig:
    def test_defaults(self) -> None:
        cfg = SmtpAlertsConfig()
        assert cfg.enabled is True
        assert "provider_error" in cfg.events
        assert "mutation_error" in cfg.events

    def test_custom_events(self) -> None:
        cfg = SmtpAlertsConfig(events=["provider_error"])
        assert cfg.events == ["provider_error"]


class TestSmtpConfig:
    def test_minimal_valid(self) -> None:
        cfg = SmtpConfig(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            **{"from": "bridge@example.com"},
            to=["admin@example.com"],
        )
        assert cfg.tls_mode == "starttls"
        assert cfg.templates_dir is None

    def test_to_must_be_nonempty(self) -> None:
        with pytest.raises(ValidationError):
            SmtpConfig(
                host="smtp.example.com", port=587,
                username="u", password="p",
                **{"from": "f@e.com"}, to=[],
            )

    def test_custom_templates_dir(self) -> None:
        cfg = SmtpConfig(
            host="smtp.example.com", port=587,
            username="u", password="p",
            **{"from": "f@e.com"}, to=["a@e.com"],
            templates_dir="/opt/my-templates",
        )
        assert cfg.templates_dir == "/opt/my-templates"


class TestWebhookConfig:
    def test_minimal_valid(self) -> None:
        cfg = WebhookConfig(url="https://hooks.example.com/abc")
        assert cfg.secret is None
        assert cfg.timeout_seconds == 10

    def test_default_events(self) -> None:
        cfg = WebhookConfig(url="https://hooks.example.com/abc")
        assert set(cfg.events) == {"device_trusted", "provider_error", "sync_complete"}

    def test_custom_events(self) -> None:
        cfg = WebhookConfig(url="https://hooks.example.com/abc", events=["provider_error"])
        assert cfg.events == ["provider_error"]


class TestNotificationsConfig:
    def test_both_optional(self) -> None:
        cfg = NotificationsConfig()
        assert cfg.smtp is None
        assert cfg.webhooks == []


class TestAppConfigNotifications:
    def test_notifications_optional(self) -> None:
        cfg = AppConfig(twingate=TwingateConfig(tenant="t", api_key="k"))
        assert cfg.notifications is None

    def test_notifications_webhooks_present(self) -> None:
        cfg = AppConfig(
            twingate=TwingateConfig(tenant="t", api_key="k"),
            notifications=NotificationsConfig(
                webhooks=[WebhookConfig(url="https://hooks.example.com/x")]
            ),
        )
        assert len(cfg.notifications.webhooks) == 1
        assert cfg.notifications.webhooks[0].url == "https://hooks.example.com/x"
