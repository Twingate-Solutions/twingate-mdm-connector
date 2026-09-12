# Configuration Reference

The bridge is configured via a YAML file combined with environment variables for secrets. By default the bridge looks for `config.yaml` in the working directory. Override this with the `CONFIG_FILE` environment variable (e.g. `CONFIG_FILE=/etc/connector/config.yaml`).

## Environment variable interpolation

Any string value in the YAML file can reference an environment variable using `${VAR_NAME}` syntax. The bridge resolves these at startup and raises an error if any referenced variable is not set.

```yaml
twingate:
  api_key: ${TWINGATE_API_KEY}   # reads from environment at startup
```

Secrets must always be passed as environment variables — never embedded as raw values in the config file.

---

## `twingate`

Connection settings for the Twingate GraphQL API.

| Key | Type | Required | Description |
|---|---|---|---|
| `tenant` | string | Yes | Twingate tenant name — the part of your Admin Console URL before `.twingate.com`. Copy it from the console: `acme` from `acme.twingate.com` (legacy) or `acme.us1` from `acme.us1.twingate.com` (shard-based, e.g. `us1`) |
| `api_key` | string | Yes | Twingate API key with Devices Read + Write scopes. Generate in Admin Console > Settings > API |

```yaml
twingate:
  tenant: ${TWINGATE_TENANT}
  api_key: ${TWINGATE_API_KEY}
```

---

## `sync`

Scheduler and sync loop behaviour.

| Key | Type | Default | Description |
|---|---|---|---|
| `interval_seconds` | int | `300` | How often to run a full sync cycle, in seconds |
| `dry_run` | bool | `false` | When `true`, log trust decisions without calling the `deviceUpdate` mutation. Use this to verify matching before a live run |
| `batch_size` | int (≥1) | `50` | Two uses: (1) page size when querying Twingate's untrusted devices, and (2) the maximum number of concurrent in-flight `deviceUpdate` mutations per cycle |

```yaml
sync:
  interval_seconds: 300
  dry_run: false
  batch_size: 50
```

---

## `trust`

Controls how compliance is evaluated and when a device is trusted.

| Key | Type | Default | Description |
|---|---|---|---|
| `mode` | `any` \| `all` | `any` | `any`: trust if the device is compliant in at least one enabled provider. `all`: trust only if it is compliant in every enabled provider |
| `require_online` | bool | `true` | Skip devices whose provider record indicates they are currently offline |
| `require_compliant` | bool | `true` | Skip devices that are not marked compliant by the provider (patch status, AV health, etc.) |
| `max_days_since_checkin` | int \| `null` | `7` | Skip devices that have not checked in with their provider within this many days. Set to `null` to disable the recency check entirely (devices are trusted regardless of when they last checked in) |

```yaml
trust:
  mode: any
  require_online: true
  require_compliant: true
  max_days_since_checkin: 7   # or `null` to disable the recency check
```

**Trust mode examples:**

- `mode: any` — recommended for migrations. A device enrolled in either NinjaOne or JumpCloud will be trusted as long as it is compliant in at least one.
- `mode: all` — use when every device must be present and compliant in all configured providers before being trusted. A device not found in any one provider is never trusted.

---

## `matching`

Device identity matching strategy.

| Key | Type | Default | Description |
|---|---|---|---|
| `primary_key` | string | `serial_number` | The field used to match Twingate devices to provider devices. Currently only `serial_number` is supported |
| `normalize` | bool | `true` | When `true`, serial numbers are normalised with `.strip().upper()` before comparison |

```yaml
matching:
  primary_key: serial_number
  normalize: true
```

---

## `logging`

Log output configuration.

| Key | Type | Default | Description |
|---|---|---|---|
| `level` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `format` | string | `json` | Output format. Only `json` (structured JSON to stdout) is currently supported |
| `timezone` | string | `UTC` | IANA timezone name (e.g. `America/New_York`, `Europe/London`). Applies to log timestamps and notification email timestamps |

```yaml
logging:
  level: INFO
  format: json
  timezone: UTC
```

---

## `providers`

A list of provider configurations. Each entry must have a `type` field that identifies the provider, and an `enabled` flag. Providers with `enabled: false` are loaded and validated but never queried.

At least one provider must be `enabled: true` for the bridge to do anything useful.

---

### `ninjaone`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"ninjaone"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `region` | string | No | `app` | Regional endpoint: `app` (US), `eu`, `ca`, `au`, `oc` |
| `client_id` | string | Yes | — | OAuth2 client ID |
| `client_secret` | string | Yes | — | OAuth2 client secret |

```yaml
- type: ninjaone
  enabled: true
  region: app
  client_id: ${NINJAONE_CLIENT_ID}
  client_secret: ${NINJAONE_CLIENT_SECRET}
```

---

### `sophos`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"sophos"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `client_id` | string | Yes | — | OAuth2 client ID from Sophos Central API Credentials |
| `client_secret` | string | Yes | — | OAuth2 client secret |

The bridge automatically discovers the tenant's regional API base URL via the Sophos `/whoami/v1` endpoint — no base URL configuration is needed.

```yaml
- type: sophos
  enabled: true
  client_id: ${SOPHOS_CLIENT_ID}
  client_secret: ${SOPHOS_CLIENT_SECRET}
```

---

### `crowdstrike`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"crowdstrike"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `cloud` | enum | No | `us-1` | Falcon cloud: `us-1`, `us-2`, `us-3`, `eu-1`, `us-gov-1`, `us-gov-2` |
| `client_id` | string | Yes | — | Falcon API client ID |
| `client_secret` | string | Yes | — | Falcon API client secret |
| `compliance.require_not_contained` | bool | No | `true` | Reject hosts whose `status` is `contained` or `containment_pending` |
| `compliance.require_full_sensor` | bool | No | `true` | Reject hosts with `reduced_functionality_mode: yes` |
| `compliance.require_live` | bool | No | `false` | Drive `is_online` from a live online-state lookup instead of "the sensor has checked in" |

The API client needs the **Hosts: Read** scope. `cloud` is a closed set — an unrecognised value is a startup error, not a request to a nonexistent host.

Both compliance checks fail **open** on an absent field: `reduced_functionality_mode` is commonly missing on macOS and Linux hosts, and a host that does not report a signal is not treated as failing it.

By default `is_online` means "this sensor has checked in at least once", so a powered-off but healthy laptop is still eligible for trust (recency is handled centrally by `trust.max_days_since_checkin`). Setting `compliance.require_live: true` adds a call to `/devices/entities/online-state/v1` per 100 hosts and requires `state == "online"` — combined with the default `trust.require_online: true` that restricts trust to hosts powered on and connected at sync time.

```yaml
- type: crowdstrike
  enabled: true
  cloud: us-1
  client_id: ${CROWDSTRIKE_CLIENT_ID}
  client_secret: ${CROWDSTRIKE_CLIENT_SECRET}
  compliance:
    require_not_contained: true
    require_full_sensor: true
    require_live: false
```

---

### `manageengine`

Supports two authentication variants: `onprem` (API token) and `cloud` (Zoho OAuth2).

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"manageengine"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `variant` | `"onprem"` \| `"cloud"` | No | `onprem` | Authentication variant |
| `base_url` | string | No (cloud) / Yes (onprem) | — | On-prem server URL, e.g. `https://uems.company.com` |
| `api_token` | string | Yes (onprem) | — | On-prem API token |
| `oauth_client_id` | string | Yes (cloud) | — | Zoho OAuth2 client ID |
| `oauth_client_secret` | string | Yes (cloud) | — | Zoho OAuth2 client secret |
| `oauth_refresh_token` | string | Yes (cloud) | — | Zoho OAuth2 refresh token |

On-prem example:

```yaml
- type: manageengine
  enabled: true
  variant: onprem
  base_url: https://uems.company.com
  api_token: ${MANAGEENGINE_API_TOKEN}
```

Cloud example:

```yaml
- type: manageengine
  enabled: true
  variant: cloud
  oauth_client_id: ${MANAGEENGINE_CLIENT_ID}
  oauth_client_secret: ${MANAGEENGINE_CLIENT_SECRET}
  oauth_refresh_token: ${MANAGEENGINE_REFRESH_TOKEN}
```

---

### `automox`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"automox"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `org_id` | string | Yes | — | Automox organisation ID (found in console URL or account settings) |
| `api_key` | string | Yes | — | Automox API key |

```yaml
- type: automox
  enabled: true
  org_id: "12345"
  api_key: ${AUTOMOX_API_KEY}
```

---

### `jumpcloud`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"jumpcloud"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `api_key` | string | Yes | — | JumpCloud admin API key |

```yaml
- type: jumpcloud
  enabled: true
  api_key: ${JUMPCLOUD_API_KEY}
```

---

### `fleetdm`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"fleetdm"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `url` | string | Yes | — | Fleet server base URL, e.g. `https://fleet.company.com` |
| `api_token` | string | Yes | — | Fleet API-only user token |

```yaml
- type: fleetdm
  enabled: true
  url: https://fleet.company.com
  api_token: ${FLEETDM_API_TOKEN}
```

---

### `mosyle`

Apple devices only (macOS, iOS, iPadOS).

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"mosyle"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `is_business` | bool | No | `false` | `true` for Mosyle Business, `false` for Mosyle Manager |
| `access_token` | string | Yes | — | Mosyle API access token |
| `email` | string | Yes | — | Mosyle admin account email |
| `password` | string | Yes | — | Mosyle admin account password |

```yaml
- type: mosyle
  enabled: true
  is_business: false
  access_token: ${MOSYLE_ACCESS_TOKEN}
  email: ${MOSYLE_EMAIL}
  password: ${MOSYLE_PASSWORD}
```

---

### `datto`

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"datto"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `api_url` | string | Yes | — | Regional API base URL, e.g. `https://pinotage-api.centrastage.net` |
| `api_key` | string | Yes | — | Datto RMM API key |
| `api_secret` | string | Yes | — | Datto RMM API secret |

```yaml
- type: datto
  enabled: true
  api_url: https://pinotage-api.centrastage.net
  api_key: ${DATTO_API_KEY}
  api_secret: ${DATTO_API_SECRET}
```

---

### `rippling`

| Key | Type | Required | Default | Description |
| --- | ---- | -------- | ------- | ----------- |
| `type` | `"rippling"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `client_id` | string | Yes | — | OAuth2 client ID |
| `client_secret` | string | Yes | — | OAuth2 client secret |

```yaml
- type: rippling
  enabled: true
  client_id: ${RIPPLING_CLIENT_ID}
  client_secret: ${RIPPLING_CLIENT_SECRET}
```

---

### `manual`

The **manual** provider is an *evaluator-archetype* provider — it does not call any external MDM/EDR. Instead, it evaluates a list of rules against Twingate's own device record and trusts devices that match. Use it for:

- Fleets with no MDM, where trust should be based on Twingate-observed attributes (hostname pattern, user email, OS, …).
- Ephemeral / on-demand VDI workstations that are spun up at runtime and never registered in an MDM.

Multiple manual blocks are supported; each one is treated as an independent contributor in trust evaluation (you compose OR-of-rule-sets by adding multiple blocks).

| Key | Type | Required | Default | Description |
|---|---|---|---|---|
| `type` | `"manual"` | Yes | — | Provider type identifier |
| `enabled` | bool | No | `false` | Enable this provider |
| `name` | string (1–64 chars) | Yes | — | Logical name used in logs and notification payloads. Must be unique across all providers (including the hardcoded names of inventory providers like `jumpcloud`, `ninjaone`). |
| `match_mode` | `all` \| `any` | No | `all` | `all`: every rule must match. `any`: at least one rule must match |
| `rules` | list of rule objects | Yes (≥1) | — | Rule set — see below |

Each rule object has:

| Key | Type | Description |
|---|---|---|
| `field` | enum | One of: `hostname`, `serial_number`, `os_name`, `os_version`, `name`, `username`, `user_email`, `device_type` |
| `check` | enum | One of: `equals`, `starts_with`, `ends_with`, `contains`, `regex`, `in`, `not_in` |
| `value` | string or list of strings | Pattern to match. `in` / `not_in` require a list; everything else requires a string |

**Field reference:**

- `hostname` — Twingate's `hostname` field for the device.
- `serial_number` — the raw serial number reported by Twingate.
- `os_name` — operating system name, e.g. `Windows 10`, `macOS`.
- `os_version` — OS version string.
- `name` — Twingate's display name for the device.
- `username` — last-logged-in username Twingate reports.
- `user_email` — primary user's email from Twingate's user record.
- `device_type` — Twingate's device type enum: `LAPTOP`, `DESKTOP`, `MOBILE`, `OTHER` (or `null` if Twingate has no value).

**Check semantics:** all checks are **case-insensitive by default** (the rule value and the device value are both folded via `str.casefold()` before comparison). For `regex`, the pattern is compiled with `re.IGNORECASE`. To opt back into case-sensitive regex, use Python's *scoped* inline flag syntax: `(?-i:^VDI-\d+$)`. The bare form `(?-i)` is **not** valid in Python 3.12 and will be rejected at config load.

**Null handling:** if the Twingate device has no value for the referenced field (`None`), every check — including `not_in` — returns `False`. A `match_mode: any` rule set against a sparse device will still fail unless at least one of its rules references a field the device actually populates.

**Match-mode semantics:**

- `all` — every rule must return true. Evaluation short-circuits on the first false.
- `any` — at least one rule must return true. Evaluation short-circuits on the first true.

**Example: VDI fleet matched by hostname + OS:**

```yaml
- type: manual
  enabled: true
  name: vdi-pool-a
  match_mode: all
  rules:
    - field: hostname
      check: starts_with
      value: VDI-POOL-A-
    - field: os_name
      check: equals
      value: Windows 10
```

**Example: executive laptops by email (any device type):**

```yaml
- type: manual
  enabled: true
  name: exec-laptops
  match_mode: any
  rules:
    - field: user_email
      check: in
      value:
        - ceo@example.com
        - cfo@example.com
```

**Important caveat — trust-check knobs do not apply to manual matches.** The global `trust.require_online`, `trust.require_compliant`, and `trust.max_days_since_checkin` settings are evaluated against the provider-reported `is_online` / `is_compliant` / `last_seen` fields. Because the manual provider has no external source, the device it synthesises is always `is_online=True`, `is_compliant=True`, and `last_seen=now()` — so those three trust-check knobs are effectively bypassed for any device matched only by a manual rule set. If you also want those checks enforced, pair the manual provider with an MDM under `trust.mode: all`.

**Composing with MDM providers:**

A device trusted by both an MDM and a manual rule set in `trust.mode: any` will list both contributors in logs and notifications, e.g. `via_providers=['jumpcloud', 'vdi-pool-a']`. Under `trust.mode: all`, the device must satisfy both — the manual rule must match AND the MDM must report the device as compliant.

---

## Health check

The bridge can expose a minimal TCP liveness endpoint. Set the `HEALTHZ_PORT` environment variable (not a config file setting) to enable it:

```bash
HEALTHZ_PORT=8080
```

Any TCP connection to the port receives a `200 OK` response. Use this as a Docker `HEALTHCHECK` or Kubernetes liveness probe.

---

## Complete example

```yaml
twingate:
  tenant: ${TWINGATE_TENANT}
  api_key: ${TWINGATE_API_KEY}

sync:
  interval_seconds: 300
  dry_run: false
  batch_size: 50

trust:
  mode: any
  require_online: true
  require_compliant: true
  max_days_since_checkin: 7

matching:
  primary_key: serial_number
  normalize: true

logging:
  level: INFO
  format: json
  timezone: UTC

providers:
  - type: ninjaone
    enabled: true
    region: app
    client_id: ${NINJAONE_CLIENT_ID}
    client_secret: ${NINJAONE_CLIENT_SECRET}

  - type: jumpcloud
    enabled: false
    api_key: ${JUMPCLOUD_API_KEY}

  - type: sophos
    enabled: false
    client_id: ${SOPHOS_CLIENT_ID}
    client_secret: ${SOPHOS_CLIENT_SECRET}

  - type: manageengine
    enabled: false
    variant: onprem
    base_url: https://uems.company.com
    api_token: ${MANAGEENGINE_API_TOKEN}

  - type: automox
    enabled: false
    org_id: "12345"
    api_key: ${AUTOMOX_API_KEY}

  - type: fleetdm
    enabled: false
    url: https://fleet.company.com
    api_token: ${FLEETDM_API_TOKEN}

  - type: mosyle
    enabled: false
    is_business: false
    access_token: ${MOSYLE_ACCESS_TOKEN}
    email: ${MOSYLE_EMAIL}
    password: ${MOSYLE_PASSWORD}

  - type: datto
    enabled: false
    api_url: https://pinotage-api.centrastage.net
    api_key: ${DATTO_API_KEY}
    api_secret: ${DATTO_API_SECRET}

  - type: rippling
    enabled: false
    client_id: ${RIPPLING_CLIENT_ID}
    client_secret: ${RIPPLING_CLIENT_SECRET}
```

---

## `notifications` (optional)

Controls outbound alerts and summaries. The entire block is optional — omitting it
disables all notifications entirely.

### Event types

Both channels use a string-based `events` list to enable/disable individual events.
Adding a future event type (e.g., `device_untrusted`) requires no schema change —
admins simply add the event name string to the relevant `events` list.

| Event name | When it fires |
| --- | --- |
| `device_trusted` | A device is set to `isTrusted: true` (or would be in dry-run) |
| `provider_error` | A provider fails to return data during a sync cycle |
| `sync_complete` | Each sync cycle ends (includes aggregate stats) |
| `mutation_error` | A trust mutation fails (sent via SMTP alert) |
| `startup_failure` | The connector exits unexpectedly (sent via SMTP alert) |

### `notifications.smtp`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | required | SMTP hostname |
| `port` | int | `587` | SMTP port |
| `username` | string | required | SMTP username |
| `password` | string | required | Use `${SMTP_PASSWORD}` |
| `from` | string | required | Sender address |
| `to` | list[string] | required | Recipient(s) — at least one |
| `tls_mode` | `starttls` \| `tls` | `starttls` | `starttls` for port 587 (STARTTLS); `tls` for port 465 (implicit TLS) |
| `templates_dir` | string | `null` | Path to custom template directory |
| `alerts.enabled` | bool | `true` | Enable immediate alert emails |
| `alerts.events` | list[string] | `[provider_error, mutation_error, startup_failure]` | Alert event types |
| `digest.enabled` | bool | `false` | Enable daily digest |
| `digest.schedule` | string | `"08:00"` | Daily send time (HH:MM) |
| `digest.timezone` | string | `"UTC"` | IANA timezone for schedule |

**Custom email templates:** Copy any file from `src/notifications/templates/` to your
`templates_dir` and edit freely. Templates use Python `string.Template` `$variable`
syntax. Variables available in each template are listed in the file's comments.

**Note:** Daily digest statistics reset on container restart.

### `notifications.webhooks`

A list of webhook destinations. Each entry fires independently. You can send the same events to multiple endpoints with different formats (e.g. raw JSON to your SIEM and Slack-formatted payloads to a channel).

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `url` | string | required | POST target URL. Use `${WEBHOOK_URL}` |
| `format` | string | `raw` | Payload format: `raw` (structured JSON), `slack`, `teams`, `discord`, `pagerduty`, `opsgenie` |
| `secret` | string | `null` | HMAC-SHA256 shared secret. Adds `X-Hub-Signature-256` header |
| `events` | list[string] | `[device_trusted, provider_error, sync_complete]` | Event types to fire |
| `timeout_seconds` | int | `10` | Per-request timeout |
| `headers` | dict[string, string] | `null` | Custom static HTTP headers added to every request (e.g. `Authorization` for OpsGenie) |
| `templates_dir` | string | `null` | Path to a directory with custom `{format}_{event_type}.json` templates. Overrides bundled templates when a matching file exists |

**Built-in formats:**

| Format | Target platform | Notes |
| --- | --- | --- |
| `raw` | Any JSON endpoint / SIEM | Original structured JSON — backward-compatible with v1 payloads |
| `slack` | Slack Incoming Webhooks | Uses `text` field with mrkdwn formatting |
| `teams` | Microsoft Teams Incoming Webhooks | MessageCard format |
| `discord` | Discord Webhooks | Uses `embeds` array with colour-coded cards |
| `pagerduty` | PagerDuty Events API v2 | Copy bundled template to `templates_dir` to insert your `routing_key` |
| `opsgenie` | OpsGenie Alerts API | Add `headers: {Authorization: "GenieKey YOUR_KEY"}` in config |

**Example — multiple destinations:**

```yaml
notifications:
  webhooks:
    - url: ${WEBHOOK_URL}
      format: raw
      secret: ${WEBHOOK_SECRET}
      events:
        - device_trusted
        - provider_error
        - sync_complete
    - url: ${SLACK_WEBHOOK_URL}
      format: slack
      events:
        - device_trusted
        - provider_error
```

**Custom templates:** Create a `{format}_{event_type}.json` file in your `templates_dir` directory. Templates use Python `string.Template` `$variable` syntax. See [docs/notifications.md](notifications.md) for the full variable reference per event type.

**Raw payload example (`device_trusted`):**

```json
{
  "event": "device_trusted",
  "timestamp": "2026-01-15T08:32:10Z",
  "device": {
    "hostname": "CORP-LAPTOP-01",
    "serial_masked": "****1234",
    "os": "Windows",
    "user_email": "alice@example.com"
  },
  "result": {
    "trusted": true,
    "providers_matched": ["ninjaone"],
    "dry_run": false
  }
}
```
