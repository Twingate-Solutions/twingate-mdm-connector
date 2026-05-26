"""Validation tests for ManualConfig / ManualRuleConfig."""

import pytest
from pydantic import ValidationError

from src.config import AppConfig, ManualConfig, ManualRuleConfig


# ---------------------------------------------------------------------------
# Rule-level validation
# ---------------------------------------------------------------------------


def test_rule_equals_with_string_value_ok() -> None:
    rule = ManualRuleConfig(field="hostname", check="equals", value="HOST-1")
    assert rule.value == "HOST-1"


def test_rule_in_with_list_value_ok() -> None:
    rule = ManualRuleConfig(field="device_type", check="in", value=["LAPTOP", "DESKTOP"])
    assert rule.value == ["LAPTOP", "DESKTOP"]


def test_rule_in_with_string_value_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualRuleConfig(field="device_type", check="in", value="LAPTOP")


def test_rule_equals_with_list_value_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualRuleConfig(field="hostname", check="equals", value=["A", "B"])


def test_rule_bad_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualRuleConfig(field="nope", check="equals", value="x")


def test_rule_bad_check_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualRuleConfig(field="hostname", check="nope", value="x")


def test_rule_bad_regex_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ManualRuleConfig(field="hostname", check="regex", value="[invalid(")
    assert "invalid regex" in str(exc.value)


def test_rule_valid_regex_accepted() -> None:
    rule = ManualRuleConfig(field="hostname", check="regex", value=r"^VDI-\d+$")
    assert rule.value == r"^VDI-\d+$"


# ---------------------------------------------------------------------------
# Block-level validation
# ---------------------------------------------------------------------------


def test_manual_config_minimal_valid() -> None:
    cfg = ManualConfig(
        type="manual",
        enabled=True,
        name="vdi",
        rules=[ManualRuleConfig(field="hostname", check="starts_with", value="VDI-")],
    )
    assert cfg.name == "vdi"
    assert cfg.match_mode == "all"  # default


def test_manual_config_empty_rules_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualConfig(type="manual", enabled=True, name="vdi", rules=[])


def test_manual_config_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ManualConfig(
            type="manual",
            enabled=True,
            name="",
            rules=[ManualRuleConfig(field="hostname", check="equals", value="x")],
        )


# ---------------------------------------------------------------------------
# Discriminated union + duplicate-name detection
# ---------------------------------------------------------------------------


def test_manual_config_discriminated_in_app_config() -> None:
    cfg = AppConfig.model_validate({
        "twingate": {"tenant": "t", "api_key": "k"},
        "providers": [
            {
                "type": "manual",
                "enabled": True,
                "name": "vdi-pool-a",
                "rules": [{"field": "hostname", "check": "starts_with", "value": "VDI-"}],
            }
        ],
    })
    assert len(cfg.providers) == 1
    assert isinstance(cfg.providers[0], ManualConfig)


def test_duplicate_provider_names_rejected() -> None:
    """Two providers with the same `name` collide in engine indexes."""
    with pytest.raises(ValidationError) as exc:
        AppConfig.model_validate({
            "twingate": {"tenant": "t", "api_key": "k"},
            "providers": [
                {
                    "type": "manual",
                    "enabled": True,
                    "name": "dup",
                    "rules": [{"field": "hostname", "check": "equals", "value": "x"}],
                },
                {
                    "type": "manual",
                    "enabled": True,
                    "name": "dup",
                    "rules": [{"field": "hostname", "check": "equals", "value": "y"}],
                },
            ],
        })
    assert "duplicate" in str(exc.value).lower()


def test_manual_name_collides_with_inventory_type_rejected() -> None:
    """A manual provider named the same as an inventory provider's hardcoded
    type (e.g. `name: jumpcloud`) would clobber the same key in the engine's
    per-provider stats map.  The validator must catch this."""
    with pytest.raises(ValidationError) as exc:
        AppConfig.model_validate({
            "twingate": {"tenant": "t", "api_key": "k"},
            "providers": [
                {
                    "type": "jumpcloud",
                    "enabled": True,
                    "api_key": "k",
                },
                {
                    "type": "manual",
                    "enabled": True,
                    "name": "jumpcloud",
                    "rules": [{"field": "hostname", "check": "equals", "value": "x"}],
                },
            ],
        })
    assert "duplicate" in str(exc.value).lower()
    assert "jumpcloud" in str(exc.value).lower()
