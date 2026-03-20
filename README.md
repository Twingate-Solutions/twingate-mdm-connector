# Twingate MDM Connector

> **Community project — not affiliated with or supported by Twingate.**
> This project is an independent, community-built tool. It is not created, endorsed, or maintained by Twingate, Inc. Twingate's support team cannot assist with issues related to this connector. Please use the [issue tracker](../../issues) for bug reports and questions.
>
> This software is provided under the [Apache License 2.0](LICENSE) — without warranty of any kind, express or implied. Use in production environments is at your own discretion and risk. See the license for the full disclaimer of warranties and limitation of liability.
>
> This project was developed with assistance from AI language model tools.

Twingate MDM Connector is open-source middleware that automatically marks devices as trusted in [Twingate](https://www.twingate.com/) by cross-referencing your MDM and EDR providers. You configure which providers to enable, and the connector runs on a schedule — querying each provider for its device inventory, matching devices to Twingate by serial number, and calling the Twingate API to set `isTrusted: true` on any device that passes your compliance rules. It runs as a stateless Docker container, requires no database, and never untrusts a device.

## How it works

On each sync cycle the connector:

1. Queries every enabled MDM/EDR provider in parallel for their device inventory.
2. Matches devices to Twingate by serial number (normalised to `strip().upper()`).
3. Marks a device as trusted in Twingate if it passes the compliance check for the configured trust mode.

```
MDM/EDR providers          twingate-mdm-connector                Twingate
──────────────────         ──────────────────────────────         ─────────
NinjaOne  ─────────┐
Sophos    ─────────┤
ManageEngine  ─────┤
Automox   ─────────┤──►  serial number match + compliance  ──►  isTrusted: true
JumpCloud ─────────┤       (never sets isTrusted: false)
FleetDM   ─────────┤
Mosyle    ─────────┤
Datto RMM ─────────┤
Rippling  ─────────┘
```

## Feature rollout

Support for providers and third-party integrations is being added and validated over time. Unless marked as tested below, a feature may be implemented but not yet verified against a live instance — behaviour may differ from what the docs describe.

### MDM / EDR providers

| Provider | Type | Tested |
| -------- | ---- | :----: |
| NinjaOne | MDM / RMM | ✅ |
| ManageEngine Endpoint Central (cloud) | MDM / RMM | ✅ |
| ManageEngine Endpoint Central (on-prem) | MDM / RMM | |
| Sophos Central | EDR | |
| Automox | RMM | |
| JumpCloud | MDM / IAM | |
| FleetDM | MDM | |
| Mosyle | MDM (Apple) | |
| Datto RMM | RMM | |
| Rippling | HR / IT | |

### Webhook destinations

| Destination | Format | Tested |
| ----------- | ------ | :----: |
| Slack | `slack` | ✅ |
| Generic JSON / SIEM | `raw` | ✅ |
| Microsoft Teams | `teams` | |
| Discord | `discord` | |
| PagerDuty | `pagerduty` | |
| OpsGenie | `opsgenie` | |

---

## Quick start

### 1. Create a config file

The only required top-level keys to get started are `twingate` and `providers`. `tenant` is the subdomain of your Twingate Admin Console URL — for `acme.twingate.com` the tenant is `acme`.

```yaml
# config.yaml
twingate:
  tenant: acme                      # your subdomain from acme.twingate.com
  api_key: ${TWINGATE_API_KEY}

trust:
  mode: any                         # trust if compliant in ANY enabled provider
  max_days_since_checkin: 7

sync:
  interval_seconds: 300
  dry_run: false                    # set true to log decisions without mutating Twingate

logging:
  level: INFO

providers:
  - type: ninjaone
    enabled: true
    region: app
    client_id: ${NINJAONE_CLIENT_ID}
    client_secret: ${NINJAONE_CLIENT_SECRET}
```

See [docs/configuration.md](docs/configuration.md) for the full reference and [docs/providers/](docs/providers/) for per-provider setup guides.

### 2. Run with Docker

```bash
docker run --rm \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e TWINGATE_API_KEY=your-key \
  -e NINJAONE_CLIENT_ID=your-id \
  -e NINJAONE_CLIENT_SECRET=your-secret \
  ghcr.io/twingate-solutions/twingate-mdm-connector:latest
```

### 3. Run with Docker Compose

```yaml
# docker-compose.yml
services:
  connector:
    image: ghcr.io/twingate-solutions/twingate-mdm-connector:latest
    restart: unless-stopped
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    env_file: .env
    environment:
      HEALTHZ_PORT: "8080"   # optional liveness probe
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:8080"]
      interval: 60s
      timeout: 5s
      retries: 3
```

## Supported providers

| Provider | Auth | Docs |
| -------- | ---- | ---- |
| NinjaOne | OAuth2 client credentials | [docs/providers/ninjaone.md](docs/providers/ninjaone.md) |
| Sophos | OAuth2 client credentials + tenant discovery | [docs/providers/sophos.md](docs/providers/sophos.md) |
| ManageEngine (cloud) | Zoho OAuth2 | [docs/providers/manageengine.md](docs/providers/manageengine.md) |
| ManageEngine (on-prem) | API token | [docs/providers/manageengine.md](docs/providers/manageengine.md) |
| Automox | API key | [docs/providers/automox.md](docs/providers/automox.md) |
| JumpCloud | API key | [docs/providers/jumpcloud.md](docs/providers/jumpcloud.md) |
| FleetDM | Bearer token | [docs/providers/fleetdm.md](docs/providers/fleetdm.md) |
| Mosyle | Access token + email + password | [docs/providers/mosyle.md](docs/providers/mosyle.md) |
| Datto RMM | OAuth2 client credentials | [docs/providers/datto.md](docs/providers/datto.md) |
| Rippling | OAuth2 client credentials | [docs/providers/rippling.md](docs/providers/rippling.md) |

## Sync behaviour

Each sync cycle follows a fetch-everything-first, compare-in-memory approach:

1. **Provider fetch (parallel)** — every enabled provider is queried concurrently. Each provider exhausts all pages of its device API before returning, so the connector holds a complete snapshot of that provider's inventory. For large fleets this may involve several paginated API calls per provider, but all providers run at the same time so the total wall-clock time is bounded by the slowest single provider.

2. **Index build** — each provider's device list is indexed into a `serial_number → device` dictionary for O(1) lookup. Serial numbers are normalised (`strip().upper()`) at index time.

3. **Twingate fetch** — after all provider indexes are ready, the connector fetches all untrusted active devices from Twingate (also fully paginated).

4. **In-memory match** — for each untrusted Twingate device, the connector looks up its serial number in each provider's index. No further API calls are made during this phase.

5. **Trust mutations** — devices that pass the trust check receive a `deviceUpdate` mutation. Failures are logged and skipped; they do not stop the cycle.

**Scale note:** this design works well for typical fleet sizes. All device records for all providers are held in memory simultaneously during matching. Each record is lightweight (a handful of strings), so even a fleet of 10,000 devices per provider adds only a few MB of RAM. If your fleet is significantly larger than that, consider filing an issue — a streaming/chunked approach could be added.

## Trust logic

- **`trust.mode: any`** — A device is trusted if it is enrolled and compliant in at least one enabled provider. Recommended for migrations or mixed environments.
- **`trust.mode: all`** — A device must be enrolled and compliant in every enabled provider that recognises it. Use when all devices are expected to be present in all configured providers.
- Devices matched in zero providers are never trusted.
- Devices last seen more than `max_days_since_checkin` days ago are skipped.
- A device that is already trusted in Twingate is never set to untrusted.
- If a provider is unavailable or returns an error, it is skipped for that cycle — the connector never crashes on provider failure.

## Configuration reference

See [docs/configuration.md](docs/configuration.md) for the full reference. Key top-level settings:

| Key | Type | Default | Description |
| --- | ---- | ------- | ----------- |
| `twingate.tenant` | string | — | Your Twingate subdomain (e.g. `acme` from `acme.twingate.com`) |
| `twingate.api_key` | string | — | Twingate API key with Devices Read + Write scopes |
| `trust.mode` | `any` \| `all` | `any` | Trust if compliant in any vs all providers |
| `trust.max_days_since_checkin` | int | `7` | Devices not seen in this many days are skipped |
| `sync.interval_seconds` | int | `300` | How often to run a sync cycle |
| `sync.dry_run` | bool | `false` | Log decisions without mutating Twingate |
| `logging.level` | string | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Environment variable interpolation

Any config value can be replaced with `${ENV_VAR}` and the connector will substitute it at startup. If a referenced variable is not set, the connector exits with an error. Secrets should always be passed as environment variables rather than embedded in the config file.

### Health check

Set `HEALTHZ_PORT=8080` (or any port) to enable a minimal HTTP server that responds `200 OK` to every request. Use this as a Docker `HEALTHCHECK` or Kubernetes liveness probe.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide and [docs/adding-a-provider.md](docs/adding-a-provider.md) to learn how to add a new MDM/EDR provider.

## Testing

See [docs/testing/overview.md](docs/testing/overview.md) for the end-to-end testing guide, which covers obtaining provider credentials, setting up a Windows test VM, and validating the full trust flow.

## Notifications

The connector can send outbound alerts and daily summaries via two optional, independently-configurable channels.

### SMTP Email

- **Error alerts** — sent immediately when a provider fails, a trust mutation fails, or the connector exits unexpectedly
- **Daily digest** — scheduled summary email at a configurable wall-clock time

**Customisable templates:** Email bodies are rendered from editable `.txt` files.
Copy any file from [`src/notifications/templates/`](src/notifications/templates/) to
your own directory, set `smtp.templates_dir` in `config.yaml`, and edit freely
(e.g., add your internal IT contact, support links, or extra context).

Serial numbers in all emails are partially masked (`****1234`) to avoid leaking device identifiers.

### HTTP Webhooks

Send signed JSON POST requests to multiple destinations with platform-native formatting. Configure one or more webhook entries under `notifications.webhooks`:

- **Multiple destinations** — send the same events to your SIEM, a Slack channel, and a PagerDuty service simultaneously
- **Built-in formats** — `raw` (structured JSON), `slack`, `teams`, `discord`, `pagerduty`, `opsgenie`
- **Custom templates** — drop `{format}_{event_type}.json` files in a `templates_dir` to tailor payloads for any platform
- **HMAC-SHA256 signing** — optional payload signing via a shared secret (`X-Hub-Signature-256` header)
- **Custom headers** — inject static HTTP headers (e.g. `Authorization` for OpsGenie)

Both channels support future event types (e.g., untrust events) without config schema changes — admins add the event name to the `events` list in `config.yaml`.

See [docs/notifications.md](docs/notifications.md) for the full notifications reference and [docs/configuration.md](docs/configuration.md) for the config schema.

## License

Apache 2.0 — see [LICENSE](LICENSE).
