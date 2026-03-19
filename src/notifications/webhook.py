"""HTTP webhook notification channel.

Sends JSON POST requests on device trust, provider error, and sync complete
events.  Optionally signs payloads with HMAC-SHA256.  Failures are non-fatal.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from src.config import WebhookConfig
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.utils.http import build_client, request_with_retry
from src.utils.logging import get_logger

logger = get_logger(__name__)

_WEBHOOK_MAX_RETRIES = 2   # 3 total attempts


class WebhookNotifier:
    """Delivers webhook payloads via signed HTTP POST.

    Each event fires only if its name appears in ``config.events``.  This
    list-based filter means future event types (e.g. ``device_untrusted``,
    ``local_trust_applied``) are automatically supported — admins just add
    the event name to their ``config.yaml`` without any code changes.
    """

    def __init__(self, config: WebhookConfig) -> None:
        self._cfg = config

    async def on_device_trusted(self, event: TrustEvent) -> None:
        """Fire ``device_trusted`` webhook if configured."""
        if "device_trusted" not in self._cfg.events:
            return
        payload = {
            "event": "device_trusted",
            "timestamp": event.timestamp.isoformat(),
            "device": {
                "hostname": event.device_name,
                "serial_masked": event.masked_serial,
                "os": event.os_name,
                "user_email": event.user_email,
            },
            "result": {
                "trusted": True,
                "providers_matched": list(event.providers),
                "dry_run": event.dry_run,
            },
        }
        await self._post(payload)

    async def on_provider_error(self, event: ProviderErrorEvent) -> None:
        """Fire ``provider_error`` webhook if configured."""
        if "provider_error" not in self._cfg.events:
            return
        payload = {
            "event": "provider_error",
            "timestamp": event.timestamp.isoformat(),
            "provider": event.provider_name,
            "error": event.error_message,
        }
        await self._post(payload)

    async def on_sync_complete(self, event: SyncCompleteEvent) -> None:
        """Fire ``sync_complete`` webhook if configured."""
        if "sync_complete" not in self._cfg.events:
            return
        payload = {
            "event": "sync_complete",
            "timestamp": event.timestamp.isoformat(),
            "cycle": event.cycle_number,
            "stats": {
                "total_untrusted": event.total_untrusted,
                "total_trusted": event.total_trusted,
                "total_skipped": event.total_skipped,
                "total_no_match": event.total_no_match,
                "total_errors": event.total_errors,
            },
            "providers": list(event.provider_names),
        }
        await self._post(payload)

    async def _post(self, payload: dict) -> None:
        """Serialize, sign, and POST the payload. Failures are non-fatal."""
        body = json.dumps(payload, default=str).encode()
        headers = {"Content-Type": "application/json"}

        if self._cfg.secret:
            sig = "sha256=" + hmac.new(
                self._cfg.secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-Hub-Signature-256"] = sig

        try:
            async with build_client(
                read_timeout=float(self._cfg.timeout_seconds),
            ) as client:
                await request_with_retry(
                    client, "POST", self._cfg.url,
                    headers=headers,
                    content=body,
                    max_retries=_WEBHOOK_MAX_RETRIES,
                )
            logger.info(
                "Webhook delivered",
                event_type=payload.get("event"),
                url=self._cfg.url,
            )
        except Exception as exc:
            logger.error(
                "Webhook delivery failed — continuing",
                event_type=payload.get("event"),
                url=self._cfg.url,
                error=str(exc),
            )
