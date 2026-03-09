# JumpCloud provider setup

## Prerequisites

You need a JumpCloud API key with read access to systems.

1. Log in to JumpCloud → click your avatar (top right) → **My API Key**.
2. Copy the key.

For organisations using **multi-tenant** JumpCloud, use the API key of the org you want to query, or create a dedicated service account.

## Configuration

```yaml
providers:
  - type: jumpcloud
    enabled: true
    api_key: ${JUMPCLOUD_API_KEY}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `api_key` | Yes | — | JumpCloud API key |

## Environment variables

| Variable | Description |
|----------|-------------|
| `JUMPCLOUD_API_KEY` | JumpCloud API key |

## Compliance logic

A device is compliant when:

- `active` is `true` — the device is enrolled and active.
- Full-disk encryption (`fde`) is either absent or `fde.active` is not explicitly `false`.

A device is online when `active` is `true`.

## Notes

- JumpCloud systems are paginated using `skip` and `limit=100`. The bridge uses `totalCount` from the response to determine when to stop.
- Only systems (macOS, Windows, Linux) report serial numbers. Mobile devices managed through JumpCloud MDM are not included in the `/systems` endpoint and will not be matched.
- The API key is sent as the `x-api-key` header.
