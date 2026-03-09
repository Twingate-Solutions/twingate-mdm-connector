# Mosyle provider setup

Mosyle is an Apple-only MDM. Two products are supported: **Mosyle Manager** (education) and **Mosyle Business**.

## Prerequisites

1. Log in to Mosyle Manager or Mosyle Business.
2. Go to **Organization → API Integration** and enable API access.
3. Copy the **Access Token** shown on that page.
4. Note the email address and password of the admin account you will use for API calls.

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
| `is_business` | No | `false` | Set `true` to use the Mosyle Business endpoint |
| `access_token` | Yes | — | Mosyle API access token |
| `email` | Yes | — | Admin account email |
| `password` | Yes | — | Admin account password |

## Environment variables

| Variable | Description |
|----------|-------------|
| `MOSYLE_ACCESS_TOKEN` | Mosyle API access token |
| `MOSYLE_EMAIL` | Admin account email |
| `MOSYLE_PASSWORD` | Admin account password |

## Compliance logic

A device is compliant when its `status` field is `enrolled`, `managed`, or `supervised`.

## Notes

- The bridge fetches **macOS** (`os=osx`) and **iOS/iPadOS** (`os=ios`) devices separately, combining results into a single list.
- Pagination uses a 1-indexed `page` field in the POST body. Fetching stops when the `devices` array is empty.
- Mosyle does not expose a direct online/offline flag — all devices are reported as online.
- The credentials (`accessToken`, `email`, `password`) are embedded in every POST request body. There is no separate token acquisition step.
- Because Mosyle only manages Apple devices, only macOS, iOS, and iPadOS serial numbers will be matched against Twingate.
