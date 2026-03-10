"""
validate-trust.py — Assert that a Twingate device is trusted or untrusted.

Queries the Twingate GraphQL API, finds the device with the given serial number,
and asserts that its isTrusted state matches the expected value.

Usage:
    python scripts/validate-trust.py \\
        --tenant mycompany \\
        --api-key $TWINGATE_API_KEY \\
        --serial "VMW-1234-5678" \\
        --expected trusted

    # Using environment variables:
    export TWINGATE_TENANT=mycompany
    export TWINGATE_API_KEY=your_api_key
    python scripts/validate-trust.py --serial "VMW-1234-5678" --expected trusted

Exit codes:
    0 — assertion passed (device found and trust state matches expected)
    1 — assertion failed (device not found, trust state mismatch, or HTTP error)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx

QUERY = """
query ListDevices($after: String) {
    devices(first: 100, after: $after) {
        pageInfo {
            hasNextPage
            endCursor
        }
        edges {
            node {
                id
                name
                serialNumber
                isTrusted
                lastConnectedAt
                user {
                    email
                }
            }
        }
    }
}
"""


def fetch_all_devices(
    client: httpx.Client,
    endpoint: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Paginate through all Twingate devices and return the full list.

    Exhausts cursor-based pagination — never assumes one page is complete.
    """
    devices: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        variables: dict[str, Any] = {}
        if cursor:
            variables["after"] = cursor

        response = client.post(
            endpoint,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            json={"query": QUERY, "variables": variables},
        )
        response.raise_for_status()

        data = response.json()
        if "errors" in data:
            errors = data["errors"]
            print(f"ERROR: GraphQL errors returned: {errors}", file=sys.stderr)
            sys.exit(1)

        page = data["data"]["devices"]
        for edge in page["edges"]:
            devices.append(edge["node"])

        page_info = page["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return devices


def normalise_serial(serial: str) -> str:
    """Normalise a serial number the same way the bridge does: strip + uppercase."""
    return serial.strip().upper()


def find_device_by_serial(
    devices: list[dict[str, Any]],
    target_serial: str,
) -> dict[str, Any] | None:
    """Find a device by normalised serial number. Returns None if not found."""
    normalised_target = normalise_serial(target_serial)
    for device in devices:
        raw_serial = device.get("serialNumber") or ""
        if normalise_serial(raw_serial) == normalised_target:
            return device
    return None


def parse_args() -> argparse.Namespace:
    """Parse and validate CLI arguments, falling back to environment variables."""
    parser = argparse.ArgumentParser(
        description="Assert that a Twingate device is trusted or untrusted.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tenant",
        default=os.environ.get("TWINGATE_TENANT"),
        help="Twingate tenant name (e.g. 'mycompany' from mycompany.twingate.com). "
             "Can also be set via TWINGATE_TENANT env var.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TWINGATE_API_KEY"),
        help="Twingate API key. Can also be set via TWINGATE_API_KEY env var.",
    )
    parser.add_argument(
        "--serial",
        required=True,
        help="Device serial number to look up (case-insensitive).",
    )
    parser.add_argument(
        "--expected",
        required=True,
        choices=["trusted", "untrusted"],
        help="Expected trust state: 'trusted' or 'untrusted'.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30).",
    )

    args = parser.parse_args()

    if not args.tenant:
        parser.error("--tenant is required (or set TWINGATE_TENANT environment variable)")
    if not args.api_key:
        parser.error("--api-key is required (or set TWINGATE_API_KEY environment variable)")

    return args


def main() -> None:
    """Run the trust validation check and exit with 0 on pass, 1 on fail."""
    args = parse_args()

    endpoint = f"https://{args.tenant}.twingate.com/api/graphql/"
    expected_trusted = args.expected == "trusted"
    target_serial_display = normalise_serial(args.serial)

    print(f"Querying Twingate API for tenant '{args.tenant}'...")
    print(f"Looking for device with serial: {target_serial_display}")
    print(f"Expected trust state: {args.expected}")
    print()

    try:
        with httpx.Client(timeout=args.timeout) as client:
            devices = fetch_all_devices(client, endpoint, args.api_key)
    except httpx.HTTPStatusError as exc:
        print(f"ERROR: HTTP {exc.response.status_code} from Twingate API: {exc}")
        sys.exit(1)
    except httpx.RequestError as exc:
        print(f"ERROR: Network error querying Twingate API: {exc}")
        sys.exit(1)

    device = find_device_by_serial(devices, args.serial)

    if device is None:
        print(f"FAIL: No device found with serial '{target_serial_display}' in Twingate.")
        print(f"  Total devices scanned: {len(devices)}")
        print()
        print("  Hint: check that the serial matches the output of:")
        print("    (Get-WmiObject Win32_BIOS).SerialNumber")
        print("  on the device (comparison is case-insensitive).")
        print()
        print("  Use scripts/dump-serial.ps1 on the VM for a formatted summary.")
        sys.exit(1)

    actual_trusted: bool = device.get("isTrusted", False)
    device_name: str = device.get("name") or "(unnamed)"
    device_id: str = device.get("id") or "(unknown)"
    user_email: str = (device.get("user") or {}).get("email") or "(no user)"
    last_connected: str = device.get("lastConnectedAt") or "(never)"

    if actual_trusted == expected_trusted:
        state_str = "trusted" if actual_trusted else "untrusted"
        print(f'PASS: Device "{device_name}" (serial: {target_serial_display}) '
              f"is {state_str}={actual_trusted} as expected.")
        print(f"  Twingate ID    : {device_id}")
        print(f"  User           : {user_email}")
        print(f"  Last connected : {last_connected}")
        sys.exit(0)
    else:
        actual_str = "trusted" if actual_trusted else "untrusted"
        print(f'FAIL: Device "{device_name}" (serial: {target_serial_display}) '
              f"is {actual_str}={actual_trusted} but expected {args.expected}={expected_trusted}.")
        print(f"  Twingate ID    : {device_id}")
        print(f"  User           : {user_email}")
        print(f"  Last connected : {last_connected}")

        if expected_trusted and not actual_trusted:
            print()
            print("  The bridge has not yet trusted this device. Check:")
            print("  1. That the bridge is running and has completed at least one sync cycle.")
            print("  2. The bridge logs for 'action': 'would_trust' (dry_run: true) or 'action': 'trusted'.")
            print("  3. That dry_run is set to false in config.yaml.")
            print("  4. That the serial in the provider UI matches this device's serial.")
        sys.exit(1)


if __name__ == "__main__":
    main()
