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
    excluded (they cannot be matched).

    When several records share a serial, the one with the most recent
    ``last_seen`` wins.  Providers routinely keep a stale record after a
    machine is reimaged, renamed, or has its agent reinstalled — the old record
    lingers alongside the new one under the same serial.  The most recent
    record reflects the device's current state; an older record must never be
    allowed to vote for (or against) it.  A record with no ``last_seen`` loses
    to any record that has one, and on an exact tie the first occurrence is
    kept.

    Args:
        devices: List of :class:`~src.providers.base.ProviderDevice` objects
            returned by a provider's ``list_devices()``.

    Returns:
        Dict mapping ``normalized_serial -> ProviderDevice``.
    """
    index: dict[str, ProviderDevice] = {}
    for device in devices:
        key = normalize_serial(device.serial_number)
        if not key:
            continue
        current = index.get(key)
        if current is None or _last_seen_sort_key(device) > _last_seen_sort_key(current):
            index[key] = device
    return index


# Sorts below every real timestamp, so a record without ``last_seen`` never
# displaces one that has it.
_NEVER_SEEN = datetime.min.replace(tzinfo=UTC)


def _last_seen_sort_key(device: ProviderDevice) -> datetime:
    """Return a timezone-aware ``last_seen`` suitable for comparison.

    Naive timestamps are treated as UTC (matching :func:`is_device_recent`);
    comparing a naive datetime with an aware one would raise ``TypeError`` and
    take the whole provider out of the sync cycle.
    """
    last_seen = device.last_seen
    if last_seen is None:
        return _NEVER_SEEN
    if last_seen.tzinfo is None:
        return last_seen.replace(tzinfo=UTC)
    return last_seen


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
    max_days_since_checkin: int | None,
) -> tuple[bool, list[str]]:
    """Decide whether a Twingate device should be trusted.

    Args:
        tg_device: The Twingate device being evaluated.
        provider_results: Mapping of ``provider_name -> ProviderDevice | None``.
            ``None`` means the device was not found in that provider.
        mode: Trust mode — ``"any"`` or ``"all"``.
        require_online: If ``True``, the device must be online in the provider.
        require_compliant: If ``True``, the device must pass compliance checks.
        max_days_since_checkin: Maximum days since last provider check-in, or
            ``None`` to disable the recency check.

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
    max_days: int | None,
) -> bool:
    """Return ``True`` if a single provider device passes all configured checks."""
    if require_online and not device.is_online:
        return False
    if require_compliant and not device.is_compliant:
        return False
    if max_days is not None and not is_device_recent(device, max_days):
        return False
    return True
