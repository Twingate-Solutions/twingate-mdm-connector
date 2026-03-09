"""Serial number normalization and device matching.

The serial number is the sole matching key between Twingate devices and
provider devices.  Normalization is mandatory before any comparison:
``strip().upper()``.

This module is intentionally small — keep all matching logic here so it is
easy to test and reason about.
"""

from datetime import UTC, datetime, timedelta

from src.providers.base import ProviderDevice
from src.twingate.models import TwingateDevice


def normalize_serial(serial: str | None) -> str | None:
    """Normalize a serial number for comparison.

    Args:
        serial: Raw serial number string, or ``None``.

    Returns:
        ``serial.strip().upper()`` when *serial* is a non-empty string, or
        ``None`` if the input is ``None`` / blank.
    """
    if not serial:
        return None
    normalized = serial.strip().upper()
    return normalized or None


def build_provider_index(
    devices: list[ProviderDevice],
) -> dict[str, ProviderDevice]:
    """Build a lookup dict keyed by normalized serial number.

    Devices whose ``serial_number`` normalizes to ``None`` are silently
    excluded (they cannot be matched).  If duplicate serials appear only the
    first occurrence is kept.

    Args:
        devices: List of :class:`~src.providers.base.ProviderDevice` objects
            returned by a provider's ``list_devices()``.

    Returns:
        Dict mapping ``normalized_serial -> ProviderDevice``.
    """
    index: dict[str, ProviderDevice] = {}
    for device in devices:
        key = normalize_serial(device.serial_number)
        if key and key not in index:
            index[key] = device
    return index


def is_device_recent(device: ProviderDevice, max_days: int) -> bool:
    """Return ``True`` if the device checked in within *max_days* days.

    If ``device.last_seen`` is ``None`` the device is considered stale
    (returns ``False``).

    Args:
        device: A normalized provider device.
        max_days: Maximum number of days since last check-in.
    """
    if device.last_seen is None:
        return False
    cutoff = datetime.now(tz=UTC) - timedelta(days=max_days)
    last_seen = device.last_seen
    # Ensure timezone-aware comparison
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    return last_seen >= cutoff


def evaluate_trust(
    tg_device: TwingateDevice,
    provider_results: dict[str, ProviderDevice | None],
    mode: str,
    require_online: bool,
    require_compliant: bool,
    max_days_since_checkin: int,
) -> tuple[bool, list[str]]:
    """Decide whether a Twingate device should be trusted.

    Args:
        tg_device: The Twingate device being evaluated.
        provider_results: Mapping of ``provider_name -> ProviderDevice | None``.
            ``None`` means the device was not found in that provider.
        mode: Trust mode — ``"any"`` or ``"all"``.
        require_online: If ``True``, the device must be online in the provider.
        require_compliant: If ``True``, the device must pass compliance checks.
        max_days_since_checkin: Maximum days since last provider check-in.

    Returns:
        A ``(should_trust, contributing_providers)`` tuple.
        ``contributing_providers`` lists the provider names that voted *yes*.
    """
    passing: list[str] = []
    failing: list[str] = []

    for provider_name, provider_device in provider_results.items():
        if provider_device is None:
            failing.append(provider_name)
            continue

        checks_pass = _check_device(
            provider_device,
            require_online=require_online,
            require_compliant=require_compliant,
            max_days=max_days_since_checkin,
        )

        if checks_pass:
            passing.append(provider_name)
        else:
            failing.append(provider_name)

    if mode == "any":
        return bool(passing), passing

    # mode == "all"
    all_providers = list(provider_results.keys())
    if not all_providers:
        return False, []
    if failing:
        return False, passing
    return True, passing


def _check_device(
    device: ProviderDevice,
    require_online: bool,
    require_compliant: bool,
    max_days: int,
) -> bool:
    """Return ``True`` if a single provider device passes all configured checks."""
    if require_online and not device.is_online:
        return False
    if require_compliant and not device.is_compliant:
        return False
    if not is_device_recent(device, max_days):
        return False
    return True
