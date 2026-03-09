# NinjaOne provider setup

## Prerequisites

You need a NinjaOne API application with the **Monitoring** scope.

1. Log in to NinjaOne → **Administration → Apps → API**.
2. Click **Add** and create an application of type **Client Credentials**.
3. Grant the **Monitoring** scope.
4. Copy the **Client ID** and **Client Secret**.

## Configuration

```yaml
providers:
  - type: ninjaone
    enabled: true
    region: app        # app | eu | ca | au | oc
    client_id: ${NINJAONE_CLIENT_ID}
    client_secret: ${NINJAONE_CLIENT_SECRET}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `region` | No | `app` | Regional endpoint prefix — `app` (US), `eu`, `ca`, `au`, `oc` |
| `client_id` | Yes | — | OAuth2 client ID |
| `client_secret` | Yes | — | OAuth2 client secret |

## Environment variables

| Variable | Description |
|----------|-------------|
| `NINJAONE_CLIENT_ID` | OAuth2 client ID |
| `NINJAONE_CLIENT_SECRET` | OAuth2 client secret |

## Compliance logic

A device is compliant when:

- `antivirus.threatStatus` is `PROTECTED` or absent.
- `patches.patchStatus` is `OK` or absent.

## Notes

- Only Windows and macOS agents report serial numbers. Mobile and network devices are skipped if they lack a serial.
- The API uses cursor-based pagination (`after` parameter). The bridge exhausts all pages automatically.
- Rate limit: 10 requests/second per organisation. The retry helper backs off on 429 responses.
