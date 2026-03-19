# Datto RMM provider setup

Datto RMM is a remote monitoring and management (RMM) platform aimed at managed service providers (MSPs). It manages Windows, macOS, and Linux endpoints across multiple client organisations.

## Getting an account

Datto RMM is sold through a partner/reseller model and does not offer a self-service free trial. To get access, contact Datto at [https://www.datto.com/products/rmm](https://www.datto.com/products/rmm) to arrange a demo or begin partner onboarding. Access is typically granted to MSPs and IT service providers rather than end-user organisations directly.

## Generating API credentials

1. Log in to the Datto RMM portal.
2. Go to **Setup → Global Settings → API**.
3. Click **Generate API Keys**.
4. Note the **API URL** displayed for your region (for example, `https://pinotage-api.centrastage.net`). This URL is also visible in your browser's address bar when you are logged in.
5. Copy the **API Key** and **API Secret** — these act as the username and password for OAuth2 token acquisition.

> Keep the API secret secure. It cannot be retrieved after the page is closed; you must regenerate the keys if it is lost.

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
| `api_url` | Yes | — | Regional API base URL for your Datto RMM instance (no trailing slash) |
| `api_key` | Yes | — | Datto RMM API key, used as the OAuth2 username |
| `api_secret` | Yes | — | Datto RMM API secret, used as the OAuth2 password |

## Regional API URLs

Datto RMM uses region-specific API hostnames. Your region is shown in the browser URL when you log in to the portal.

| Region | URL |
|--------|-----|
| Pinotage (US default) | `https://pinotage-api.centrastage.net` |
| Merlot | `https://merlot-api.centrastage.net` |
| Concord | `https://concord-api.centrastage.net` |
| Vidal | `https://vidal-api.centrastage.net` |
| Syrah | `https://syrah-api.centrastage.net` |
| Zinfandel | `https://zinfandel-api.centrastage.net` |

## Environment variables

| Variable          | Description                           |
|-------------------|---------------------------------------|
| `DATTO_API_KEY`   | Datto RMM API key (OAuth2 username)   |
| `DATTO_API_SECRET`| Datto RMM API secret (OAuth2 password)|

## Compliance logic

A device is compliant when **all** of the following conditions are met:

- `patchStatus` is `FULLY_PATCHED`, `NOT_SUPPORTED`, `UP_TO_DATE`, or absent.
- `antivirusStatus` is `PROTECTED`, `NOT_APPLICABLE`, `NONE`, or absent.
- `rebootRequired` is `false` or absent.

Devices that fail any one of these checks are considered non-compliant and will not be trusted in Twingate.

## Notes

- Authentication uses the OAuth2 **password grant flow**: the API key is submitted as the username and the API secret as the password. Access tokens are valid for approximately 100 hours (360,000 seconds). The bridge caches tokens and refreshes them automatically before expiry.
- Pagination follows `pageDetails.nextPageUrl` in each response. Fetching continues until that field is `null`. Each page contains up to 250 devices.
- The Datto RMM API enforces a rate limit of 600 read requests per minute. The bridge's retry helper backs off automatically on `429 Too Many Requests` responses.
- Datto RMM is designed for MSP use, so a single instance may manage devices across many client organisations. The bridge fetches all devices visible to the API key regardless of which client site they belong to.
