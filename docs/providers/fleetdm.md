# FleetDM provider setup

## Prerequisites

You need a FleetDM API token. Fleet supports two ways to obtain one:

- **User API token:** Log in to the Fleet UI → your avatar → **My account → API token**.
- **Service account:** Create a dedicated Fleet user with **Observer** role and use their API token.

The token requires at minimum **Observer** access to read host details and policy results.

## Configuration

```yaml
providers:
  - type: fleetdm
    enabled: true
    base_url: https://fleet.corp.example.com
    api_token: ${FLEETDM_API_TOKEN}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `base_url` | Yes | — | URL of your Fleet instance (no trailing slash) |
| `api_token` | Yes | — | Fleet API token |

## Environment variables

| Variable | Description |
|----------|-------------|
| `FLEETDM_API_TOKEN` | Fleet API token |

## Compliance logic

A device is compliant when **all** policies applied to it have `response == "pass"`. A device with no policies is considered compliant.

Policy results are fetched from the per-host detail endpoint (`/api/v1/fleet/hosts/{id}`). The bridge fetches host details concurrently (up to 20 in parallel) to keep sync times low.

## Notes

- The bridge first paginates `/api/v1/fleet/hosts` (1-indexed pages, stops when `meta.has_next_results` is `false`), then concurrently fetches details for each host.
- If a detail call fails, the host falls back to the list data (no policy information — treated as compliant).
- Serial number is read from `hardware_serial`.
- Fleet does not expose a direct online/offline flag — all devices are reported as online.
- Self-hosted Fleet instances must have a valid TLS certificate, or you must configure a trusted CA in the Docker environment.
