"""Configuration loading and validation for twingate-device-trust-bridge.

Loads a YAML config file with ${ENV_VAR} interpolation and validates it
against Pydantic models. Secrets must live in environment variables — never
as raw values in YAML.
"""

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

# TLS mode for SMTP connections.
# ``starttls`` — plain connection upgraded via STARTTLS (port 587, most providers)
# ``tls``      — implicit TLS from the start (port 465)
TlsMode = Literal["starttls", "tls"]

import yaml
from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class TwingateConfig(BaseModel):
    """Twingate API connection settings."""

    tenant: str
    api_key: str


class SyncConfig(BaseModel):
    """Scheduler and sync loop settings."""

    interval_seconds: int = 300
    dry_run: bool = False
    batch_size: int = 50


class MatchingConfig(BaseModel):
    """Device matching strategy settings."""

    primary_key: str = "serial_number"
    normalize: bool = True


class TrustConfig(BaseModel):
    """Trust evaluation logic settings."""

    mode: Literal["any", "all"] = "any"
    require_online: bool = True
    require_compliant: bool = True
    max_days_since_checkin: int = 7


class LoggingConfig(BaseModel):
    """Logging output settings."""

    level: str = "INFO"
    format: str = "json"
    timezone: str = "UTC"  # IANA timezone name — used for log timestamps and notification emails


# ---------------------------------------------------------------------------
# Notification configs
# ---------------------------------------------------------------------------


class SmtpAlertsConfig(BaseModel):
    """Per-event controls for immediate SMTP alert emails.

    The ``events`` list mirrors the ``webhook.events`` pattern — add any
    event name string to enable that alert type.  Future event types
    (e.g. ``device_untrusted``) are supported without schema changes.
    """

    enabled: bool = True
    events: list[str] = Field(
        default_factory=lambda: ["provider_error", "mutation_error", "startup_failure"]
    )


class SmtpDigestConfig(BaseModel):
    """Controls the daily digest summary email."""

    enabled: bool = False
    schedule: str = "08:00"   # HH:MM in the configured timezone
    timezone: str = "UTC"


class SmtpConfig(BaseModel):
    """SMTP email channel configuration.

    Templates are loaded from ``templates_dir`` if set, otherwise from the
    bundled defaults in ``src/notifications/templates/``.  Templates are
    plain-text files using Python ``string.Template`` ``$variable`` syntax.
    """

    host: str
    port: int = 587
    username: str
    password: str
    from_address: str = Field(alias="from", default="")
    to: list[str] = Field(min_length=1)
    tls_mode: TlsMode = "starttls"
    alerts: SmtpAlertsConfig = Field(default_factory=SmtpAlertsConfig)
    digest: SmtpDigestConfig = Field(default_factory=SmtpDigestConfig)
    templates_dir: str | None = None   # override bundled templates

    model_config = {"populate_by_name": True}


class WebhookConfig(BaseModel):
    """HTTP webhook channel configuration.

    The ``events`` list controls which event types fire a webhook POST.
    Future event types are supported without schema changes — just add the
    new event name string to this list.

    The ``format`` field selects the payload shape.  ``raw`` (the default)
    produces the original structured JSON.  Any other value (e.g. ``slack``,
    ``teams``, ``discord``, ``pagerduty``, ``opsgenie``) loads a bundled
    JSON template that is rendered with ``string.Template`` substitution.

    ``headers`` lets admins inject static HTTP headers (e.g. an
    ``Authorization`` header for OpsGenie or PagerDuty).

    ``templates_dir`` overrides the bundled template directory — drop custom
    ``{format}_{event_type}.json`` files there to tailor payloads.
    """

    url: str
    secret: str | None = None
    events: list[str] = Field(
        default_factory=lambda: ["device_trusted", "provider_error", "sync_complete"]
    )
    timeout_seconds: int = 10
    format: str = "raw"
    headers: dict[str, str] | None = None
    templates_dir: str | None = None


class NotificationsConfig(BaseModel):
    """Top-level notifications block.  Both channels are independently optional."""

    smtp: SmtpConfig | None = None
    webhooks: list[WebhookConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider configs — one model per provider, discriminated on `type`
# ---------------------------------------------------------------------------


class NinjaOneConfig(BaseModel):
    """NinjaOne (NinjaRMM) provider configuration."""

    type: Literal["ninjaone"]
    enabled: bool = False
    region: str = "app"
    client_id: str
    client_secret: str


class SophosConfig(BaseModel):
    """Sophos Central provider configuration."""

    type: Literal["sophos"]
    enabled: bool = False
    client_id: str
    client_secret: str


class ManageEngineCloudComplianceConfig(BaseModel):
    """Compliance check settings for the ManageEngine cloud variant.

    Both checks are evaluated together — a device must pass every enabled
    check to be considered compliant.

    Attributes:
        require_installed: Require ``installation_status == 22`` (agent is
            installed and managed).  Enabled by default.
        require_live: Require ``computer_live_status == 1`` (machine is
            currently reachable by the Endpoint Central agent).  Disabled by
            default — enable this for stricter checks that reject devices
            whose agent has gone silent.
    """

    require_installed: bool = True
    require_live: bool = False


class ManageEngineConfig(BaseModel):
    """ManageEngine Endpoint Central provider configuration.

    Supports two auth variants:
      - ``onprem``: API token auth against a self-hosted instance.
      - ``cloud``:  OAuth2 (Zoho) auth against the cloud service.
    """

    type: Literal["manageengine"]
    enabled: bool = False
    variant: Literal["onprem", "cloud"] = "onprem"
    base_url: str | None = None

    # on-prem auth
    api_token: str | None = None

    # cloud (Zoho OAuth2) auth
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_refresh_token: str | None = None
    oauth_token_url: str | None = None  # defaults to accounts.zoho.com; set for other regions

    # cloud compliance (omitting this block uses the defaults)
    compliance: ManageEngineCloudComplianceConfig = ManageEngineCloudComplianceConfig()

    @model_validator(mode="after")
    def _check_auth_fields(self) -> "ManageEngineConfig":
        if not self.enabled:
            return self
        if self.variant == "onprem" and not self.api_token:
            raise ValueError("ManageEngine onprem variant requires api_token")
        if self.variant == "cloud" and not all(
            [self.oauth_client_id, self.oauth_client_secret, self.oauth_refresh_token]
        ):
            raise ValueError(
                "ManageEngine cloud variant requires oauth_client_id, "
                "oauth_client_secret, and oauth_refresh_token"
            )
        return self


class AutomoxConfig(BaseModel):
    """Automox provider configuration."""

    type: Literal["automox"]
    enabled: bool = False
    org_id: str
    api_key: str


class JumpCloudConfig(BaseModel):
    """JumpCloud provider configuration."""

    type: Literal["jumpcloud"]
    enabled: bool = False
    api_key: str


class FleetDMConfig(BaseModel):
    """FleetDM provider configuration."""

    type: Literal["fleetdm"]
    enabled: bool = False
    url: str
    api_token: str


class MosyleConfig(BaseModel):
    """Mosyle (Apple MDM) provider configuration."""

    type: Literal["mosyle"]
    enabled: bool = False
    is_business: bool = False
    access_token: str
    email: str
    password: str


class DattoConfig(BaseModel):
    """Datto RMM provider configuration."""

    type: Literal["datto"]
    enabled: bool = False
    api_url: str
    api_key: str
    api_secret: str


class RipplingConfig(BaseModel):
    """Rippling HR/IT platform provider configuration."""

    type: Literal["rippling"]
    enabled: bool = False
    client_id: str
    client_secret: str


# Discriminated union of all provider config types
ProviderConfig = Annotated[
    NinjaOneConfig
    | SophosConfig
    | ManageEngineConfig
    | AutomoxConfig
    | JumpCloudConfig
    | FleetDMConfig
    | MosyleConfig
    | DattoConfig
    | RipplingConfig,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Root application configuration."""

    twingate: TwingateConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    trust: TrustConfig = Field(default_factory=TrustConfig)
    providers: list[ProviderConfig] = Field(default_factory=list)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifications: NotificationsConfig | None = None

    @property
    def enabled_providers(self) -> list[ProviderConfig]:
        """Return only the providers with ``enabled: true``."""
        return [p for p in self.providers if p.enabled]


# ---------------------------------------------------------------------------
# YAML loading with ${VAR} interpolation
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate_env_vars(value: Any) -> Any:
    """Recursively resolve ``${ENV_VAR}`` patterns in strings.

    Raises ``KeyError`` if a referenced variable is not set in the environment.
    """
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name not in os.environ:
                raise KeyError(
                    f"Environment variable '{var_name}' referenced in config "
                    "is not set"
                )
            return os.environ[var_name]

        return _ENV_VAR_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_interpolate_env_vars(item) for item in value]

    return value


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate the application configuration.

    Reads the YAML file at *config_path*, resolves ``${ENV_VAR}`` placeholders
    from the current environment, then validates the result against
    :class:`AppConfig`.

    Args:
        config_path: Path to the YAML config file. Defaults to ``config.yaml``
            in the current working directory.

    Returns:
        A fully validated :class:`AppConfig` instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError: If a ``${VAR}`` placeholder references an unset env var.
        pydantic.ValidationError: If the config fails schema validation.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    resolved = _interpolate_env_vars(raw)
    return AppConfig.model_validate(resolved)
