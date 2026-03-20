"""
test-webhook-notifications.py — Fire test webhook payloads for every configured webhook destination.

Loads config.yaml (or the file set by CONFIG_FILE), constructs a real WebhookNotifier
for each entry in the ``notifications.webhooks`` list, and fires one POST per event
type in each webhook's configured events list.

Usage:
    python scripts/test-webhook-notifications.py
    CONFIG_FILE=my-config.yaml python scripts/test-webhook-notifications.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.notifications.base import ProviderErrorEvent, SyncCompleteEvent, TrustEvent
from src.notifications.webhook import WebhookNotifier


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def run() -> None:
    config_path = os.environ.get("CONFIG_FILE", "config.yaml")
    print(f"Loading config from: {config_path}")

    config = load_config(config_path)

    if config.notifications is None or not config.notifications.webhooks:
        print("\nNo webhook configuration found in config.yaml.")
        print("Add a 'notifications.webhooks' list to test webhook notifications.")
        sys.exit(1)

    total_sent = 0
    total_skipped = 0

    for idx, wh_cfg in enumerate(config.notifications.webhooks, start=1):
        notifier = WebhookNotifier(wh_cfg)

        print(f"\n{'=' * 60}")
        print(f"Webhook {idx}/{len(config.notifications.webhooks)}")
        print(f"  URL:      {wh_cfg.url}")
        print(f"  Format:   {wh_cfg.format}")
        print(f"  Signing:  {'yes (HMAC-SHA256)' if wh_cfg.secret else 'no'}")
        print(f"  Events:   {wh_cfg.events}")
        print(f"  Timeout:  {wh_cfg.timeout_seconds}s")
        if wh_cfg.headers:
            print(f"  Headers:  {list(wh_cfg.headers.keys())}")
        print()

        sent = 0
        skipped = 0

        # --- device_trusted ---
        if "device_trusted" in wh_cfg.events:
            print("  Sending device_trusted event...")
            await notifier.on_device_trusted(TrustEvent(
                device_id="dev-test-001",
                device_name="TEST-LAPTOP",
                serial_number="TESTSERIAL1234",
                os_name="Windows",
                user_email="test.user@example.com",
                providers=("test-provider",),
                timestamp=_now(),
                dry_run=False,
            ))
            print("    Done.")
            sent += 1
        else:
            print("  Skipping device_trusted (not in events list).")
            skipped += 1

        # --- provider_error ---
        if "provider_error" in wh_cfg.events:
            print("  Sending provider_error event...")
            await notifier.on_provider_error(ProviderErrorEvent(
                provider_name="test-provider",
                error_message="This is a test payload — no real error occurred.",
                timestamp=_now(),
            ))
            print("    Done.")
            sent += 1
        else:
            print("  Skipping provider_error (not in events list).")
            skipped += 1

        # --- sync_complete ---
        if "sync_complete" in wh_cfg.events:
            print("  Sending sync_complete event...")
            await notifier.on_sync_complete(SyncCompleteEvent(
                total_untrusted=42,
                total_trusted=7,
                total_skipped=3,
                total_no_match=32,
                total_errors=0,
                provider_names=("ninjaone", "manageengine"),
                cycle_number=0,
                timestamp=_now(),
            ))
            print("    Done.")
            sent += 1
        else:
            print("  Skipping sync_complete (not in events list).")
            skipped += 1

        print(f"\n  Webhook {idx}: {sent} sent, {skipped} skipped.")
        total_sent += sent
        total_skipped += skipped

    print(f"\n{'=' * 60}")
    print(f"Total: {total_sent} payload(s) sent, {total_skipped} skipped across "
          f"{len(config.notifications.webhooks)} webhook(s).")


if __name__ == "__main__":
    asyncio.run(run())
