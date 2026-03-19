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

## Step 4: Verify the API connection and list untrusted devices

Before configuring any providers, verify that your API key and tenant are correct and that the bridge can reach the Twingate GraphQL API.

First, install the only required dependency:

```bash
pip install httpx
```

Then run the Twingate client smoke test:

```bash
python scripts/test-twingate-client.py \
  --tenant your-tenant \
  --api-key $TWINGATE_API_KEY
```

This script fetches all untrusted **active** devices (archived devices are excluded) and pretty-prints them as JSON. A successful run looks like:

```text
Connecting to https://your-tenant.twingate.com/api/graphql/

{"count": 1, "event": "Fetched untrusted devices from Twingate", ...}

--- 1 untrusted device(s) ---

{
  "id": "RGV2aWNlOjE2MzU4NA==",
  "name": "DESKTOP-ABC123",
  "serial_number": "VMW-1234-5678",
  "os_name": "WINDOWS",
  "is_trusted": false,
  "active_state": "ACTIVE",
  ...
}
```

Note the `serial_number` field — this is what the bridge normalises (`.strip().upper()`) and compares against provider serials. If `serial_number` is `null`, the bridge will skip that device entirely.

If the device is not listed, see the troubleshooting note in [windows-vm-setup.md](windows-vm-setup.md) — the Twingate client must have authenticated and connected at least once.

### Confirm a specific device by serial

To confirm a single device by serial number:

```bash
python scripts/validate-trust.py \
  --tenant your-tenant \
  --api-key $TWINGATE_API_KEY \
  --serial "YOUR-SERIAL-HERE" \
  --expected untrusted
```

Output on success:

```text
PASS: Device "DESKTOP-ABC123" (serial: VMW-1234-5678) is untrusted=False as expected.
  Twingate ID    : RGV2aWNlOjE2MzU4NA==
  User           : you@example.com
  Last connected : (never)
```

## Step 5: Verify the trust mutation

Before adding any providers, confirm the bridge can actually call the `deviceUpdate` mutation and mark a device trusted. This isolates Twingate API issues from provider issues.

```bash
python scripts/test-trust-mutation.py \
  --tenant your-tenant \
  --api-key $TWINGATE_API_KEY \
  --serial "YOUR-SERIAL-HERE"
```

Output on success:

```text
Found: 'DESKTOP-ABC123' (id=RGV2aWNlOjE2MzU4NA==, serial=VMW-1234-5678)
Calling trust_device mutation...
PASS: 'DESKTOP-ABC123' is now isTrusted=True
```

After running this, verify in the Admin Console that the device shows **Security: Device instance verified**. Then **manually untrust** the device before continuing (Admin Console > Devices > click device > Actions > **Remove Trust**) so it will be picked up by the bridge in a later step.

If this step fails with a `403` or GraphQL error, check that the API key has **Devices: Write** scope (Step 3 above).

## Step 6: Dry-run first pass

Always do a dry run before running the bridge in live mode. This confirms the bridge can see the device and would trust it, without making any changes.

1. In `config.yaml`, set `dry_run: true`.
2. Run the bridge (see [local-run.md](local-run.md) or [docker-run.md](docker-run.md)).
3. In the logs, look for a line containing `"action": "would_trust"` with the correct `device_serial`.
4. Confirm the device is still `isTrusted: false` in the Admin Console — the dry run must not mutate anything.

**Example dry-run log line:**

```json
{"event": "device_would_trust", "action": "would_trust", "device_serial": "VMW-1234-5678", "twingate_device_id": "RGV2aWNlOjE2MzU4NA==", "provider": "jumpcloud", "level": "info", "timestamp": "2025-03-10T10:00:05Z"}
```

## Step 7: Confirm trust after a live run

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
