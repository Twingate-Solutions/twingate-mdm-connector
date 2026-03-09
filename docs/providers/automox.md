# Automox provider setup

## Prerequisites

You need an Automox API key.

1. Log in to Automox → **Settings → API Keys**.
2. Click **Generate API Key**.
3. Copy the key.

## Configuration

```yaml
providers:
  - type: automox
    enabled: true
    api_key: ${AUTOMOX_API_KEY}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `api_key` | Yes | — | Automox API key |

## Environment variables

| Variable | Description |
|----------|-------------|
| `AUTOMOX_API_KEY` | Automox API key |

## Compliance logic

A device is compliant when **both** conditions are met:

- `is_compatible` is `true` (the OS is supported by Automox).
- `pending_patches` is `0` (no outstanding patches).

A device is online when `status.agent_status` equals `"connected"`.

## Notes

- Automox returns all devices for the organisation associated with the API key.
- If you use multiple Automox **organisations**, you will need separate config entries (one per API key).
- Pagination uses 0-indexed pages with `limit=500`. The bridge fetches all pages automatically.
- Devices without a `serial_number` field are silently skipped.
