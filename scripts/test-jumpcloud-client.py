"""
test-jumpcloud-client.py — Smoke-test the JumpCloud provider against the real API.

Fetches all managed systems and pretty-prints the first few, then summarises
serial numbers, compliance status, and last-contact times.

Usage:
    python scripts/test-jumpcloud-client.py --api-key jca_xxx
    # or via env var:
    JUMPCLOUD_API_KEY=jca_xxx python scripts/test-jumpcloud-client.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import JumpCloudConfig
from src.providers.jumpcloud import JumpCloudProvider
from src.utils.logging import configure_logging


async def run(api_key: str) -> None:
    configure_logging("DEBUG")

    print("\n=== JumpCloud API smoke test ===\n")

    # --- Raw API response ---
    import httpx
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url="https://console.jumpcloud.com/api") as client:
        r = await client.get("/systems", headers=headers, params={"limit": 10, "skip": 0})
        r.raise_for_status()
        raw = r.json()

    print("=== Raw API response (first page) ===\n")
    print(json.dumps(raw, indent=2))
    print()

    # --- Provider-parsed devices ---
    config = JumpCloudConfig(type="jumpcloud", enabled=True, api_key=api_key)
    provider = JumpCloudProvider(config)

    await provider.authenticate()
    devices = await provider.list_devices()

    print(f"\n--- {len(devices)} device(s) returned (provider-parsed) ---\n")

    import dataclasses
    preview_count = min(5, len(devices))
    for d in devices[:preview_count]:
        print(json.dumps(dataclasses.asdict(d), indent=2, default=str))
        print()

    if len(devices) > preview_count:
        print(f"... {len(devices) - preview_count} more device(s) not shown\n")

    compliant = [d for d in devices if d.is_compliant]
    online = [d for d in devices if d.is_online]
    with_serial = [d for d in devices if d.serial_number]

    print("=== Summary ===")
    print(f"  Total devices:       {len(devices)}")
    print(f"  With serial number:  {len(with_serial)}")
    print(f"  Online:              {len(online)}")
    print(f"  Compliant:           {len(compliant)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the JumpCloud provider.")
    parser.add_argument("--api-key", default=os.environ.get("JUMPCLOUD_API_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        parser.error("--api-key is required (or set JUMPCLOUD_API_KEY)")

    asyncio.run(run(args.api_key))


if __name__ == "__main__":
    main()
