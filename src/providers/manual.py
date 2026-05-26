"""Manual (evaluator-archetype) provider plugin.

Unlike inventory providers, the manual provider does not fetch an external
device list.  Instead, the engine calls :meth:`evaluate_device` once per
Twingate device with the device record itself; the provider returns a
synthesised :class:`ProviderDevice` if its rules match, or ``None`` otherwise.

Rules are evaluated case-insensitively by default (``str.casefold``).  For
regex rules, callers can opt into case-sensitive matching with the inline
``(?-i)`` flag.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

import structlog

from src.config import ManualConfig, ManualRuleConfig
from src.providers.base import ProviderDevice, ProviderPlugin
from src.twingate.models import TwingateDevice

log = structlog.get_logger()


def _get_user_email(d: TwingateDevice) -> str | None:
    return d.user.email if d.user else None


# Maps the ManualField string to the function that pulls the value out of a
# TwingateDevice.  The validator on ManualField already restricts to these
# eight keys, so a missing key would be a developer bug, not a config bug.
_FIELD_ACCESSORS: dict[str, Callable[[TwingateDevice], str | None]] = {
    "hostname":      lambda d: d.hostname,
    "serial_number": lambda d: d.serial_number,
    "os_name":       lambda d: d.os_name,
    "os_version":    lambda d: d.os_version,
    "name":          lambda d: d.name,
    "username":      lambda d: d.username,
    "user_email":    _get_user_email,
    "device_type":   lambda d: d.device_type,
}


class _CompiledRule:
    """A ManualRuleConfig pre-compiled for fast per-device evaluation.

    Stores the field accessor and, for regex checks, a compiled
    :class:`re.Pattern` so the pattern isn't re-compiled per device.
    """

    __slots__ = ("rule", "_accessor", "_regex")

    def __init__(self, rule: ManualRuleConfig) -> None:
        self.rule = rule
        self._accessor = _FIELD_ACCESSORS[rule.field]
        self._regex: re.Pattern[str] | None = None
        if rule.check == "regex":
            assert isinstance(rule.value, str)
            # Same flags as the validator in ManualRuleConfig._check_value_shape.
            self._regex = re.compile(rule.value, flags=re.IGNORECASE)

    def matches(self, device: TwingateDevice) -> bool:
        value = self._accessor(device)
        if value is None:
            return False
        match self.rule.check:
            case "equals":
                assert isinstance(self.rule.value, str)
                return value.casefold() == self.rule.value.casefold()
            case "starts_with":
                assert isinstance(self.rule.value, str)
                return value.casefold().startswith(self.rule.value.casefold())
            case "ends_with":
                assert isinstance(self.rule.value, str)
                return value.casefold().endswith(self.rule.value.casefold())
            case "contains":
                assert isinstance(self.rule.value, str)
                return self.rule.value.casefold() in value.casefold()
            case "regex":
                assert self._regex is not None
                return self._regex.search(value) is not None
            case "in":
                assert isinstance(self.rule.value, list)
                folded = value.casefold()
                return any(folded == v.casefold() for v in self.rule.value)
            case "not_in":
                assert isinstance(self.rule.value, list)
                folded = value.casefold()
                return all(folded != v.casefold() for v in self.rule.value)
        return False


class ManualProvider(ProviderPlugin):
    """Evaluator-archetype provider — applies rule sets to Twingate devices."""

    def __init__(self, config: ManualConfig) -> None:
        self._config = config
        self._compiled = [_CompiledRule(r) for r in config.rules]

    @property
    def name(self) -> str:
        return self._config.name

    async def authenticate(self) -> None:
        """No external API — nothing to authenticate."""
        return None

    async def list_devices(self) -> list[ProviderDevice]:
        """Manual providers don't fetch inventories; engine uses evaluate_device."""
        return []

    def determine_compliance(self, device: dict) -> bool:
        """Unused — kept to satisfy the ABC; manual providers decide compliance
        per-device in :meth:`evaluate_device`.
        """
        return False

    async def evaluate_device(
        self, tg_device: TwingateDevice
    ) -> ProviderDevice | None:
        """Apply the rule set to the Twingate device.

        Returns a synthesised ``ProviderDevice`` if the rules match (per the
        configured ``match_mode``), or ``None`` otherwise.  In ``any`` mode
        evaluation short-circuits on the first matching rule, so
        ``raw["matched_fields"]`` contains exactly one entry; in ``all`` mode
        it lists every rule's field in order.
        """
        matched_fields: list[str] = []
        if self._config.match_mode == "all":
            for cr in self._compiled:
                if not cr.matches(tg_device):
                    return None
                matched_fields.append(cr.rule.field)
        else:  # "any"
            any_match = False
            for cr in self._compiled:
                if cr.matches(tg_device):
                    matched_fields.append(cr.rule.field)
                    any_match = True
                    break
            if not any_match:
                return None

        serial = (tg_device.serial_number or "").strip().upper()
        log.debug(
            "manual rule matched",
            rule_set=self._config.name,
            matched_fields=matched_fields,
            twingate_device_id=tg_device.id,
            hostname=tg_device.hostname,
        )
        return ProviderDevice(
            serial_number=serial,
            hostname=tg_device.hostname,
            os_name=tg_device.os_name,
            os_version=tg_device.os_version,
            is_online=True,
            is_compliant=True,
            last_seen=datetime.now(tz=UTC),
            raw={
                "rule_set": self._config.name,
                "matched_fields": matched_fields,
            },
        )
