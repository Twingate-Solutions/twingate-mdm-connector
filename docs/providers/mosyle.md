# Mosyle provider setup

Mosyle is an Apple-only MDM platform. Two products are supported: **Mosyle Manager** (aimed at educational institutions) and **Mosyle Business** (aimed at companies). Both manage macOS, iOS, and iPadOS devices.

## Getting an account

- **Mosyle Manager** (education): Free trial available at [https://business.mosyle.com/](https://business.mosyle.com/).
- **Mosyle Business** (companies): Free trial available at [https://business.mosyle.com/](https://business.mosyle.com/).

Both products are Apple-only — only macOS, iOS, and iPadOS serial numbers will ever be matched against Twingate.

## Generating API credentials

1. Log in to Mosyle Manager or Mosyle Business as an admin.
2. Go to **Organization → API Integration**.
3. Toggle **Enable API Access** on.
4. Copy the **Access Token** displayed on that page.
5. Note the **email address** and **password** of the admin account you will use for API calls — these are sent with every request.

> The access token, email, and password are all required. There is no separate OAuth2 token acquisition step; the credentials are embedded in each POST request body.

## Configuration

```yaml
providers:
  - type: mosyle
    enabled: true
    is_business: false       # true for Mosyle Business, false for Mosyle Manager
    access_token: ${MOSYLE_ACCESS_TOKEN}
    email: ${MOSYLE_EMAIL}
    password: ${MOSYLE_PASSWORD}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `is_business` | No | `false` | Set `true` to target the Mosyle Business endpoint; `false` for Mosyle Manager |
| `access_token` | Yes | — | Mosyle API access token from the API Integration page |
| `email` | Yes | — | Email address of the admin account used for API calls |
| `password` | Yes | — | Password of the admin account used for API calls |

## Environment variables

| Variable | Description |
|----------|-------------|
| `MOSYLE_ACCESS_TOKEN` | Mosyle API access token |
| `MOSYLE_EMAIL` | Admin account email address |
| `MOSYLE_PASSWORD` | Admin account password |

## Compliance logic

A device is compliant when its `status` field is `enrolled`, `managed`, or `supervised`.

## Notes

- The bridge fetches **macOS** (`os=osx`) and **iOS/iPadOS** (`os=ios`) devices in separate requests, then combines the results into a single list before matching.
- Pagination uses a 1-indexed `page` field in the POST request body. Fetching stops when the `devices` array in the response is empty.
- Mosyle does not expose a direct online/offline flag — all devices are treated as online for the purposes of this bridge.
- The credentials (`accessToken`, `email`, `password`) are embedded in every POST request body. There is no separate token acquisition or refresh step.
- Because Mosyle only manages Apple devices, only macOS, iOS, and iPadOS serial numbers will be matched against Twingate. Windows and Android devices managed through other providers are unaffected.
