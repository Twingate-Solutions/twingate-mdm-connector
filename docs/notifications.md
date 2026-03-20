# Notifications Reference

The connector can send outbound alerts and daily summaries via two optional, independently-configurable channels: **SMTP email** and **HTTP webhooks**. Both channels are non-fatal -- if delivery fails the connector logs the error and continues.

## Enabling notifications

Add a `notifications:` block to your `config.yaml`. Omitting the block entirely disables all notifications. Each channel (smtp, webhooks) is independently optional.

```yaml
notifications:
  smtp:
    # ...
  webhooks:
    - url: https://hooks.slack.com/services/T00/B00/xxx
      format: slack
```

---

## SMTP email

### Configuration

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | required | SMTP hostname |
| `port` | int | `587` | SMTP port |
| `username` | string | required | SMTP username |
| `password` | string | required | Use `${SMTP_PASSWORD}` |
| `from` | string | required | Sender address |
| `to` | list[string] | required | Recipient(s) -- at least one |
| `tls_mode` | `starttls` \| `tls` | `starttls` | `starttls` for port 587 (STARTTLS upgrade); `tls` for port 465 (implicit TLS) |
| `templates_dir` | string | `null` | Path to custom email template directory |
| `alerts.enabled` | bool | `true` | Enable immediate alert emails |
| `alerts.events` | list[string] | `[provider_error, mutation_error, startup_failure]` | Which events trigger alert emails |
| `digest.enabled` | bool | `false` | Enable daily digest summary |
| `digest.schedule` | string | `"08:00"` | Daily send time (HH:MM) |
| `digest.timezone` | string | `"UTC"` | IANA timezone for the schedule |

### TLS modes

- **`starttls`** (default) -- connects on port 587, upgrades to TLS via STARTTLS. Works with most providers (Gmail, Microsoft 365, Amazon SES).
- **`tls`** -- implicit TLS from connection start on port 465. Use when your provider requires it.

### Alert events

Immediate emails are sent for events in the `alerts.events` list:

- `provider_error` -- a provider fails to return data during a sync cycle
- `mutation_error` -- a Twingate trust mutation fails
- `startup_failure` -- the connector exits unexpectedly

### Daily digest

When `digest.enabled: true`, a summary email is sent daily at `digest.schedule` (in `digest.timezone`). The digest includes aggregate stats across all sync cycles since the last digest (or container start). Statistics reset on container restart.

### Custom email templates

Copy any file from `src/notifications/templates/` to your `templates_dir` and edit freely. Templates use Python `string.Template` `$variable` syntax. Each template file contains comments listing the available variables.

Serial numbers are always partially masked (`****1234`) in all notifications.

---

## HTTP webhooks

### Configuration

Each entry in the `webhooks` list is an independent destination. Events are fired to all configured webhooks in parallel.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `url` | string | required | POST target URL |
| `format` | string | `raw` | Payload format (see built-in formats below) |
| `secret` | string | `null` | HMAC-SHA256 shared secret. Adds `X-Hub-Signature-256` header |
| `events` | list[string] | `[device_trusted, provider_error, sync_complete]` | Event types to fire |
| `timeout_seconds` | int | `10` | Per-request timeout |
| `headers` | dict[string, string] | `null` | Custom static HTTP headers (e.g. `Authorization`) |
| `templates_dir` | string | `null` | Path to custom webhook template directory |

### Built-in formats

| Format | Target platform | Notes |
| --- | --- | --- |
| `raw` | Any JSON consumer / SIEM | Structured JSON -- backward-compatible with v1 payloads |
| `slack` | Slack Incoming Webhooks | Uses `text` field with mrkdwn formatting |
| `teams` | Microsoft Teams Incoming Webhooks | MessageCard format with colour-coded theme |
| `discord` | Discord Webhooks | `embeds` array with colour-coded cards |
| `pagerduty` | PagerDuty Events API v2 | Requires `routing_key` -- copy template to `templates_dir` to set it |
| `opsgenie` | OpsGenie Alerts API | Add `headers: {Authorization: "GenieKey YOUR_KEY"}` |

### Multiple destinations

```yaml
notifications:
  webhooks:
    # Raw JSON to a SIEM / log collector
    - url: ${SIEM_WEBHOOK_URL}
      format: raw
      secret: ${WEBHOOK_SECRET}
      events:
        - device_trusted
        - provider_error
        - sync_complete

    # Slack channel for device trust notifications
    - url: ${SLACK_WEBHOOK_URL}
      format: slack
      events:
        - device_trusted
        - provider_error

    # OpsGenie for error alerting
    - url: https://api.opsgenie.com/v2/alerts
      format: opsgenie
      headers:
        Authorization: "GenieKey ${OPSGENIE_KEY}"
      events:
        - provider_error
```

### Platform setup notes

**Slack:** Create an Incoming Webhook in your Slack workspace (Apps > Incoming Webhooks). Use the webhook URL as the `url` field. Set `format: slack`.

**Microsoft Teams:** Create an Incoming Webhook connector on your Teams channel. Use the generated URL. Set `format: teams`.

**Discord:** In your Discord channel settings, go to Integrations > Webhooks. Create a webhook and use the URL. Set `format: discord`.

**PagerDuty:** Use `url: https://events.pagerduty.com/v2/enqueue` and set `format: pagerduty`. The bundled PagerDuty templates contain a placeholder `routing_key`. Copy the templates to your `templates_dir` and replace `YOUR_INTEGRATION_KEY_HERE` with your actual PagerDuty integration key.

**OpsGenie:** Use `url: https://api.opsgenie.com/v2/alerts` and set `format: opsgenie`. Add your GenieKey via the `headers` field: `headers: {Authorization: "GenieKey ${OPSGENIE_KEY}"}`.

### Event types

| Event | Fires when |
| --- | --- |
| `device_trusted` | A device is set to `isTrusted: true` (or would be in dry-run mode) |
| `provider_error` | A provider fails to return data during a sync cycle |
| `sync_complete` | Each sync cycle ends (includes aggregate stats) |

### Custom webhook templates

Create a `{format}_{event_type}.json` file in your `templates_dir` directory to override any bundled template or create entirely new formats. Templates use Python `string.Template` `$variable` syntax with `safe_substitute` (unmatched variables are left as-is rather than raising an error).

File naming pattern: `{format}_{event_type}.json`

Examples: `slack_device_trusted.json`, `myformat_sync_complete.json`

The template search order is:

1. `templates_dir/{format}_{event_type}.json` (if `templates_dir` is set and the file exists)
2. Bundled templates in `src/notifications/webhook_templates/`

---

## Template variable reference

### `device_trusted` event

| Variable | Type | Description |
| --- | --- | --- |
| `$event_type` | string | Always `device_trusted` |
| `$timestamp` | string | ISO 8601 timestamp |
| `$device_hostname` | string | Device name / hostname |
| `$device_serial_masked` | string | Partially masked serial number (`****1234`) |
| `$device_os` | string | Operating system name |
| `$device_user_email` | string | User email associated with the device |
| `$providers_matched` | string | Comma-separated list of providers that matched |
| `$dry_run` | string | `true` or `false` |

### `provider_error` event

| Variable | Type | Description |
| --- | --- | --- |
| `$event_type` | string | Always `provider_error` |
| `$timestamp` | string | ISO 8601 timestamp |
| `$provider_name` | string | Name of the failed provider |
| `$error_message` | string | Error description |

### `sync_complete` event

| Variable | Type | Description |
| --- | --- | --- |
| `$event_type` | string | Always `sync_complete` |
| `$timestamp` | string | ISO 8601 timestamp |
| `$total_untrusted` | string | Number of untrusted devices found |
| `$total_trusted` | string | Number of devices trusted this cycle |
| `$total_skipped` | string | Number of devices skipped |
| `$total_no_match` | string | Number of devices with no provider match |
| `$total_errors` | string | Number of errors during the cycle |
| `$provider_names` | string | Comma-separated list of active providers |
| `$cycle_number` | string | Sync cycle number |
| `$num_cycles` | string | Always `1` (reserved for future use) |

---

## Testing your configuration

Two test scripts are included to verify notification delivery without running a full sync:

```bash
# Test webhook delivery (fires one event per type to each configured webhook)
python scripts/test-webhook-notifications.py

# Test SMTP delivery (fires all alert types + optional digest)
python scripts/test-smtp-notifications.py
```

Both scripts load `config.yaml` (or the file specified by `CONFIG_FILE`) and use your real credentials, so they will send actual notifications to your configured endpoints.
