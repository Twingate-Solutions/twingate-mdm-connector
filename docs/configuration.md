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
| `tenant` | string | Yes | Twingate tenant name — the subdomain from your Admin Console URL. E.g. `acme` from `acme.twingate.com` |
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
| `batch_size` | int | `50` | Number of devices to fetch per GraphQL pagination page when querying Twingate |

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
| `max_days_since_checkin` | int | `7` | Skip devices that have not checked in with their provider within this many days |

```yaml
trust:
  mode: any
  require_online: true
  require_compliant: true
  max_days_since_checkin: 7
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

```yaml
logging:
  level: INFO
  format: json
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
