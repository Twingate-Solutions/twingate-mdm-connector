# Datto RMM provider setup

## Prerequisites

You need a Datto RMM API key and secret.

1. Log in to Datto RMM → **Setup → Global Settings → API**.
2. Click **Generate API Keys**.
3. Note the **API URL** for your region (e.g. `https://pinotage-api.centrastage.net`), and copy the **API Key** and **API Secret**.

## Configuration

```yaml
providers:
  - type: datto
    enabled: true
    api_url: https://pinotage-api.centrastage.net
    api_key: ${DATTO_API_KEY}
    api_secret: ${DATTO_API_SECRET}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `api_url` | Yes | — | Regional API base URL (no trailing slash) |
| `api_key` | Yes | — | Datto RMM API key (used as OAuth2 username) |
| `api_secret` | Yes | — | Datto RMM API secret (used as OAuth2 password) |

## Regional API URLs

| Region | URL |
|--------|-----|
| Pinotage (US default) | `https://pinotage-api.centrastage.net` |
| Merlot | `https://merlot-api.centrastage.net` |
| Concord | `https://concord-api.centrastage.net` |
| Vidal | `https://vidal-api.centrastage.net` |
| Syrah | `https://syrah-api.centrastage.net` |
| Zinfandel | `https://zinfandel-api.centrastage.net` |

Your region is shown in the browser URL when you log in to the Datto RMM portal.

## Environment variables

| Variable | Description |
|----------|-------------|
| `DATTO_API_KEY` | Datto RMM API key |
| `DATTO_API_SECRET` | Datto RMM API secret |

## Compliance logic

A device is compliant when **all** conditions are met:

- `patchStatus` is `FULLY_PATCHED`, `NOT_SUPPORTED`, `UP_TO_DATE`, or absent.
- `antivirusStatus` is `PROTECTED`, `NOT_APPLICABLE`, `NONE`, or absent.
- `rebootRequired` is `false` or absent.

## Notes

- Authentication uses the OAuth2 **password grant**: the API key is the username and the API secret is the password. Tokens are valid for approximately 100 hours (360,000 seconds) and are cached and refreshed automatically.
- Pagination follows `pageDetails.nextPageUrl` in each response until the field is `null`. Each page contains up to 250 devices.
- Rate limit: 600 read requests/minute. The retry helper backs off automatically on 429 responses.
