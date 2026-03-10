# Provider Credentials for Testing

This document covers how to obtain test credentials for each of the 8 supported providers. For each provider, you need credentials with read access to the device inventory. The bridge never writes to provider APIs.

## Quick reference

| Provider | Account type | Sign-up URL | Auth type |
|---|---|---|---|
| NinjaOne | 30-day free trial | https://www.ninjaone.com/free-trial/ | OAuth2 client credentials |
| Sophos Central | 30-day free trial | https://www.sophos.com/en-us/free-trials/sophos-central | OAuth2 client credentials |
| ManageEngine Endpoint Central | Cloud trial / on-prem free (≤25 devices) | https://www.manageengine.com/products/desktop-central/free-trial.html | Cloud: Zoho OAuth2 / On-prem: API token |
| Automox | Free trial | https://www.automox.com/free-trial | API key (Bearer) |
| JumpCloud | **Free tier (up to 10 devices, no expiry)** | https://www.jumpcloud.com/signup | API key header |
| FleetDM | **OSS self-hosted, free** | https://fleetdm.com/docs/deploy/deploy-fleet-on-docker | API token (Bearer) |
| Mosyle | Manager trial | https://business.mosyle.com/ | Token + email + password |
| Datto RMM | Partner/MSP demo (sales-assisted) | https://www.datto.com/products/rmm/ | OAuth2 (api_key + api_secret) |

## Where to start

**JumpCloud** and **FleetDM** are the easiest providers to start with:

- **JumpCloud** — free tier, no trial expiry, single API key, device enrolment takes under a minute.
- **FleetDM** — fully open-source, runs locally via Docker, no account sign-up required.

Skip **Datto RMM** (requires contacting sales) and **Mosyle** (Apple-only — skip for Windows VM testing) unless you specifically need to test those providers.

---

## NinjaOne

**Account type:** 30-day free trial
**Sign-up:** https://www.ninjaone.com/free-trial/

**Minimum required permissions:** Devices — View (read-only)

**Generating credentials:**

1. Log into the NinjaOne console.
2. Go to **Administration > Apps > API**.
3. Click **Add application**.
4. Select **Machine-to-Machine** application type.
5. Set the scope to include **Monitoring** (which grants device read access).
6. Save — you'll receive a **Client ID** and **Client Secret**.

**Config fields:**

```yaml
- type: ninjaone
  enabled: true
  client_id: ${NINJAONE_CLIENT_ID}
  client_secret: ${NINJAONE_CLIENT_SECRET}
  region: app   # options: app (US), eu, ca, au, oc
```

**Regional base URLs:**

| Region key | Location | Base URL |
|---|---|---|
| `app` | US | `https://app.ninjarmm.com` |
| `eu` | EU | `https://eu.ninjarmm.com` |
| `ca` | Canada | `https://ca.ninjarmm.com` |
| `au` | Australia | `https://au.ninjarmm.com` |
| `oc` | Oceania | `https://oc.ninjarmm.com` |

---

## Sophos Central

**Account type:** 30-day free trial
**Sign-up:** https://www.sophos.com/en-us/free-trials/sophos-central

**Minimum required permissions:** Computer and Server Protection — read access

**Generating credentials:**

1. Log into Sophos Central Admin.
2. Go to **My Products > General Settings > API Credentials**.
3. Click **Add Credential**.
4. Select role: **Service Principal ReadOnly** (or equivalent with Computers read access).
5. Save — you'll receive a **Client ID** and **Client Secret**.

**Gotcha:** The bridge calls `/whoami/v1` at `https://id.sophos.com/api/v2/oauth2/token` to discover the tenant's `dataRegion` and constructs the API base URL dynamically. You do not need to provide the base URL manually.

**Config fields:**

```yaml
- type: sophos
  enabled: true
  client_id: ${SOPHOS_CLIENT_ID}
  client_secret: ${SOPHOS_CLIENT_SECRET}
```

---

## ManageEngine Endpoint Central

**Account type:**
- **Cloud:** Free trial at https://www.manageengine.com/products/desktop-central/free-trial.html
- **On-prem:** Download and install locally — free for up to 25 managed computers

**Minimum required permissions:** Inventory read access; Patch Management read access

### On-prem auth (API token)

1. Log into the ManageEngine Desktop Central web console.
2. Go to **Admin > API Token**.
3. Generate a new token.
4. Note the server URL (e.g. `http://your-server:8383`).

```yaml
- type: manageengine
  enabled: true
  cloud: false
  server_url: http://your-server:8383
  api_token: ${MANAGEENGINE_API_TOKEN}
```

### Cloud auth (Zoho OAuth2)

1. Go to the Zoho API Console: https://api-console.zoho.com
2. Create a new **Self Client** application.
3. Under **Generate Code**, select scope: `SDPOnDemand.assets.READ` (or the ManageEngine Endpoint Central equivalent).
4. Exchange the one-time code for a refresh token using Zoho's OAuth flow.
5. Note the **Client ID**, **Client Secret**, and **Refresh Token**.

```yaml
- type: manageengine
  enabled: true
  cloud: true
  client_id: ${MANAGEENGINE_CLIENT_ID}
  client_secret: ${MANAGEENGINE_CLIENT_SECRET}
  refresh_token: ${MANAGEENGINE_REFRESH_TOKEN}
```

**Gotcha:** On-prem and cloud use completely different authentication mechanisms. Set `cloud: true` or `cloud: false` explicitly in config.

---

## Automox

**Account type:** Free trial
**Sign-up:** https://www.automox.com/free-trial

**Minimum required permissions:** Devices — read access

**Generating credentials:**

1. Log into the Automox console.
2. Go to **Account Settings > API Keys**.
3. Click **Create API Key**.
4. Name it (e.g. `twingate-bridge`) and save — no scope selection is needed (all keys have full account access).

```yaml
- type: automox
  enabled: true
  api_key: ${AUTOMOX_API_KEY}
```

---

## JumpCloud

**Account type:** Free tier — up to 10 devices, no expiry
**Sign-up:** https://www.jumpcloud.com/signup

**Minimum required permissions:** Systems — read (the admin API key has full read access by default)

**Generating credentials:**

1. Log into the JumpCloud Admin Console.
2. Click your name/avatar in the top-right corner.
3. Select **API Settings**.
4. Copy the **API Key** shown. (You can regenerate it here if needed.)

```yaml
- type: jumpcloud
  enabled: true
  api_key: ${JUMPCLOUD_API_KEY}
```

---

## FleetDM

**Account type:** Open-source, self-hosted — completely free
**Deploy docs:** https://fleetdm.com/docs/deploy/deploy-fleet-on-docker

**Minimum required permissions:** Hosts read access; Policies read access

**Deploying Fleet locally:**

```bash
# Pull and start Fleet (requires Docker)
docker run -d --name fleet-mysql \
  -e MYSQL_ROOT_PASSWORD=toor \
  -e MYSQL_DATABASE=fleet \
  -e MYSQL_USER=fleet \
  -e MYSQL_PASSWORD=fleet \
  mysql:8.0

docker run -d --name fleet-redis redis:7

docker run -d --name fleet \
  -p 8080:8080 \
  -e FLEET_MYSQL_ADDRESS=fleet-mysql:3306 \
  -e FLEET_MYSQL_DATABASE=fleet \
  -e FLEET_MYSQL_USERNAME=fleet \
  -e FLEET_MYSQL_PASSWORD=fleet \
  -e FLEET_REDIS_ADDRESS=fleet-redis:6379 \
  --link fleet-mysql --link fleet-redis \
  fleetdm/fleet:latest fleet serve \
  --dev_license
```

Alternatively, follow the official Compose-based setup in the Fleet docs for a simpler start.

**Generating an API-only token:**

```bash
# After Fleet is running, use fleetctl
fleetctl config set --address https://localhost:8080
fleetctl setup   # first-time only — creates admin user
fleetctl user create --name "bridge" --email bridge@example.com --password "Password123!" --api-only
fleetctl login --email bridge@example.com --password "Password123!"
# The token is stored in ~/.fleet/config — copy the token value
```

**Gotcha:** Devices must have the `osquery`/`fleetd` agent installed to appear in Fleet. See [windows-vm-setup.md](windows-vm-setup.md) for agent installation.

```yaml
- type: fleetdm
  enabled: true
  base_url: https://localhost:8080
  api_token: ${FLEETDM_API_TOKEN}
```

---

## Mosyle

**Account type:** Manager free trial
**Sign-up:** https://business.mosyle.com/

**Important:** Mosyle manages **Apple devices only** (macOS, iOS, tvOS). This provider cannot be tested with a Windows VM. Use a Mac (physical or virtual) for Mosyle testing.

**Minimum required permissions:** Device Management — read access

**Generating credentials:**

1. Log into the Mosyle Manager console.
2. Go to **Settings > API Access**.
3. Click **Generate Token**.
4. Note the **access_token**, and also the admin **email** and **password** — all three are required for API calls.

```yaml
- type: mosyle
  enabled: true
  access_token: ${MOSYLE_ACCESS_TOKEN}
  email: ${MOSYLE_EMAIL}
  password: ${MOSYLE_PASSWORD}
```

---

## Datto RMM

**Account type:** Partner/MSP demo environment — requires contacting Datto sales
**Request:** https://www.datto.com/products/rmm/

There is no self-service free trial for Datto RMM. You must request a demo/partner environment through Datto sales or be an existing MSP partner.

**Minimum required permissions:** Devices — read access

**Generating credentials:**

1. Log into the Datto RMM Admin Portal.
2. Go to **Setup > API Credentials**.
3. Create a new API key pair — you'll receive an **API Key** and **API Secret**.

**Regional endpoints:**

| Platform | Base URL |
|---|---|
| pinotage | `https://pinotage-api.centrastage.net` |
| merlot | `https://merlot-api.centrastage.net` |
| zinfandel | `https://zinfandel-api.centrastage.net` |
| concord | `https://concord-api.centrastage.net` |
| syrah | `https://syrah-api.centrastage.net` |
| halberd | `https://halberd-api.centrastage.net` |
| centennial | `https://centennial-api.centrastage.net` |

```yaml
- type: datto
  enabled: true
  api_key: ${DATTO_API_KEY}
  api_secret: ${DATTO_API_SECRET}
  platform: pinotage   # your platform name
```
