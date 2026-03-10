# Twingate Setup

This document covers creating a Twingate test network, generating an API key with the correct scopes, confirming the test VM appears as an untrusted device, and verifying bridge output after a run.

## Step 1: Create a Twingate account

1. Sign up at https://www.twingate.com/ — the free starter plan supports up to 5 users.
2. After signup, a **Network** is created automatically. Note the **tenant name** — it appears in the Admin Console URL as `https://{tenant}.twingate.com`. For example, if your Admin Console is `https://acme.twingate.com`, your tenant is `acme`.
3. For testing, you can use the default network or create a dedicated test network.

## Step 2: Create a test resource

Twingate only displays devices in the Admin Console after they have been used to access a resource. You need a resource assigned to the test user's group for the VM to appear.

1. In the Admin Console, go to **Resources > Add Resource**.
2. Create a dummy resource — for example:
   - **Address:** `10.0.0.1` (a private IP that doesn't need to actually exist)
   - **Name:** `Test Resource`
3. Create a **Group** (or use an existing one) and assign the resource to it.
4. Add the test user to that group.
5. On the VM, open the Twingate client and attempt to connect to the resource (or simply authenticate — the device should appear in the Admin Console once the client is active and connected).

## Step 3: Generate an API key

The bridge needs an API key with **Devices read** and **Devices write** permissions.

1. In the Admin Console, go to **Settings > API > Create API Key**.
2. Set a name (e.g. `twingate-bridge-test`).
3. Enable the following scopes:
   - **Devices: Read** — required to list untrusted devices
   - **Devices: Write** — required to call the `deviceUpdate` trust mutation
4. Save. **Copy the API key immediately** — it is shown only once.

Note your:
- **Tenant name** (e.g. `acme`)
- **API key** (the long token you just copied)

These go into the bridge config:

```yaml
twingate:
  tenant: acme
  api_key: ${TWINGATE_API_KEY}
```

```bash
export TWINGATE_API_KEY=your_api_key_here
```

## Step 4: Confirm the device appears as untrusted

### Via Admin Console

Go to **Admin Console > Devices**. The VM should appear with **Trust Status: Untrusted**.

If the device is not listed, see the troubleshooting note in [windows-vm-setup.md](windows-vm-setup.md) — the Twingate client must have authenticated and connected at least once.

### Via GraphQL API

You can confirm via the API directly using curl:

```bash
curl -s -X POST \
  "https://{tenant}.twingate.com/api/graphql/" \
  -H "X-API-KEY: {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "{ devices(filter: { isTrusted: { eq: false } }) { edges { node { id name serialNumber isTrusted lastConnectedAt } } } }"
  }' | python -m json.tool
```

Replace `{tenant}` and `{api_key}` with your values. The response should include the test VM with `"isTrusted": false` and a non-null `serialNumber`.

**Example response:**

```json
{
  "data": {
    "devices": {
      "edges": [
        {
          "node": {
            "id": "RGV2aWNlOjE2MzU4NA==",
            "name": "DESKTOP-ABC123",
            "serialNumber": "VMW-1234-5678",
            "isTrusted": false,
            "lastConnectedAt": "2025-03-10T09:55:00Z"
          }
        }
      ]
    }
  }
}
```

Note the `serialNumber` field. This is what the bridge will normalise (`.strip().upper()`) and compare against provider serials.

## Step 5: Dry-run first pass

Always do a dry run before running the bridge in live mode. This confirms the bridge can see the device and would trust it, without making any changes.

1. In `config.yaml`, set `dry_run: true`.
2. Run the bridge (see [local-run.md](local-run.md) or [docker-run.md](docker-run.md)).
3. In the logs, look for a line containing `"action": "would_trust"` with the correct `device_serial`.
4. Confirm the device is still `isTrusted: false` in the Admin Console — the dry run must not mutate anything.

**Example dry-run log line:**

```json
{"event": "device_would_trust", "action": "would_trust", "device_serial": "VMW-1234-5678", "twingate_device_id": "RGV2aWNlOjE2MzU4NA==", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:00:05Z"}
```

## Step 6: Confirm trust after a live run

After switching to `dry_run: false` and running the bridge:

### Via Admin Console

Go to **Admin Console > Devices** — the device should show **Trust Status: Trusted**.

### Via GraphQL API

```bash
curl -s -X POST \
  "https://{tenant}.twingate.com/api/graphql/" \
  -H "X-API-KEY: {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "{ devices(filter: { isTrusted: { eq: true } }) { edges { node { id name serialNumber isTrusted } } } }"
  }' | python -m json.tool
```

The test VM should now appear in the `isTrusted: true` query results.

### Via validation script

```bash
python scripts/validate-trust.py \
  --tenant your-tenant \
  --api-key $TWINGATE_API_KEY \
  --serial "VMW-1234-5678" \
  --expected trusted
```

### In bridge logs

Look for:

```json
{"event": "device_trusted", "action": "trusted", "device_serial": "VMW-1234-5678", "twingate_device_id": "RGV2aWNlOjE2MzU4NA==", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:01:05Z"}
```

## Step 7: Reset for re-testing

To return the device to untrusted status for another test run:

1. In the Admin Console, go to **Devices**.
2. Click the device name.
3. Click **Actions > Remove Trust** (or the equivalent in the Admin Console version you have).
4. The device is now untrusted again and will be picked up on the next bridge sync cycle.

Note: removing trust in the Admin Console does not affect the provider — the device remains enrolled and compliant in the MDM/EDR. The bridge will re-trust it on the next cycle.
