# End-to-End Validation Checklist

Work through this checklist in order to validate the full happy-path flow: a Windows VM enrolled in a provider → bridge matches it by serial number → device is marked `isTrusted: true` in Twingate.

Each step is labelled:
- **[You do this]** — requires a real account, UI access, a physical or virtual machine, or other manual action.
- **[Claude Code can help]** — can be scripted, automated, or validated programmatically.

---

## Phase 1: Account Setup

- [ ] **[You do this]** Sign up for a provider trial or use the free tier (see [credentials.md](credentials.md) — start with JumpCloud or FleetDM).
  > Verify by: you can log into the provider admin console.

- [ ] **[You do this]** Generate API credentials for the bridge (provider-specific steps in [credentials.md](credentials.md)).
  > Verify by: credentials are shown in the console and copied to a secure location.

- [ ] **[You do this]** Sign up for a Twingate account and identify the tenant name (see [twingate-setup.md](twingate-setup.md)).
  > Verify by: you can access the Twingate Admin Console at `https://{tenant}.twingate.com`.

- [ ] **[You do this]** Generate a Twingate API key with **Devices: Read** and **Devices: Write** scopes (Admin Console > Settings > API > Create API Key).
  > Verify by: the key is shown — copy it immediately, it is displayed only once.

---

## Phase 2: VM Setup

- [ ] **[You do this]** Spin up a Windows 10/11 Pro or Enterprise VM (see [windows-vm-setup.md](windows-vm-setup.md) for hypervisor notes and minimum spec).
  > Verify by: VM boots and has internet access.

- [ ] **[You do this]** Install the provider agent on the VM (agent install steps in [windows-vm-setup.md](windows-vm-setup.md)).
  > Verify by: the device appears in the provider admin console within ~5 minutes.

- [ ] **[You do this]** Run the serial number check on the VM:
  ```powershell
  (Get-WmiObject Win32_BIOS).SerialNumber
  ```
  Write down the output.
  > Verify by: output is non-empty and is not a placeholder like `"To Be Filled By O.E.M."`.

- [ ] **[Claude Code can help]** Run `scripts/dump-serial.ps1` on the VM for a formatted summary including a placeholder warning:
  ```powershell
  .\scripts\dump-serial.ps1
  ```

- [ ] **[You do this]** Find the device's serial number in the provider admin console (see the per-provider table in [windows-vm-setup.md](windows-vm-setup.md) for where to find it).
  > Verify by: the serial shown in the provider UI matches the PowerShell output (case-insensitive comparison). If they differ, the bridge will log `"action": "no_match"` — investigate before continuing.

- [ ] **[You do this]** Install the Twingate client on the VM, sign in with a test user account, and connect to a Twingate resource at least once (see [twingate-setup.md](twingate-setup.md), Step 2).
  > Verify by: the Twingate icon is active in the system tray.

- [ ] **[You do this]** Confirm the VM appears in the Twingate Admin Console under **Devices** with **Trust Status: Untrusted**.

- [ ] **[Claude Code can help]** Confirm the device is untrusted via the GraphQL API:
  ```bash
  python scripts/validate-trust.py \
    --tenant your-tenant \
    --api-key $TWINGATE_API_KEY \
    --serial "YOUR-SERIAL-HERE" \
    --expected untrusted
  ```
  > Verify by: script prints `PASS`.

---

## Phase 3: Twingate API Pre-verification

Verify the Twingate API key works end-to-end — including the trust mutation — before introducing any provider complexity. This makes it much easier to isolate failures later.

- [ ] **[Claude Code can help]** List all untrusted active devices via the API:
  ```bash
  python scripts/test-twingate-client.py \
    --tenant your-tenant \
    --api-key $TWINGATE_API_KEY
  ```
  > Verify by: script prints the device list and the test VM appears with `"active_state": "ACTIVE"` and a non-null `serial_number`. If `serial_number` is `null`, the bridge will not be able to match this device.

- [ ] **[Claude Code can help]** Verify the trust mutation works by trusting the device directly:
  ```bash
  python scripts/test-trust-mutation.py \
    --tenant your-tenant \
    --api-key $TWINGATE_API_KEY \
    --serial "YOUR-SERIAL-HERE"
  ```
  > Verify by: script prints `PASS: '...' is now isTrusted=True`. Then check the Twingate Admin Console — the device should show **Security: Device instance verified**.

- [ ] **[You do this]** Manually untrust the device in the Admin Console before continuing: Devices > click device > Actions > **Remove Trust**.
  > Verify by: device shows Trust Status: Untrusted again.

---

## Phase 4: Configuration

- [ ] **[You do this]** Create a `config.yaml` based on `config.yaml.example`. Minimum required fields: `twingate.tenant`, `twingate.api_key`, and at least one enabled provider. See [local-run.md](local-run.md) or [docker-run.md](docker-run.md) for a minimal example.
  > Verify by: file exists and all `${VAR}` references have corresponding environment variables set.

- [ ] **[You do this]** Set the required environment variables.

  Linux / macOS:
  ```bash
  export TWINGATE_API_KEY=your_key
  export JUMPCLOUD_API_KEY=your_key   # adjust per provider
  ```

  Windows (PowerShell):
  ```powershell
  $env:TWINGATE_API_KEY = "your_key"
  $env:JUMPCLOUD_API_KEY = "your_key"
  ```
  > Verify by: `echo $TWINGATE_API_KEY` (or `$env:TWINGATE_API_KEY`) returns a non-empty value.

---

## Phase 5: Dry Run

- [ ] **[You do this]** Confirm `dry_run: true` is set in `config.yaml`.

- [ ] **[You do this]** Start the bridge:
  - Local: `python -m src.main`
  - Docker: `docker compose up -d` then `docker logs -f twingate-mdm-connector`

- [ ] **[You do this]** Watch the logs for `"action": "would_trust"` with the correct `device_serial`.
  > Verify by: a log line like the following appears within one sync interval (default: 60 seconds):
  ```json
  {"event": "device_would_trust", "action": "would_trust", "device_serial": "YOUR-SERIAL", ...}
  ```

- [ ] **[You do this]** Confirm the device is **still untrusted** in the Twingate Admin Console — the dry run must not mutate anything.
  > Verify by: Admin Console > Devices shows Trust Status: Untrusted.

---

## Phase 6: Live Run

- [ ] **[You do this]** Change `dry_run: true` to `dry_run: false` in `config.yaml`. Restart the bridge.

- [ ] **[You do this]** Watch the logs for `"action": "trusted"` with the correct `device_serial` and `twingate_device_id`.
  > Verify by: a log line like the following appears:
  ```json
  {"event": "device_trusted", "action": "trusted", "device_serial": "YOUR-SERIAL", "twingate_device_id": "...", ...}
  ```

- [ ] **[You do this]** Confirm the device now shows **Trust Status: Trusted** in the Twingate Admin Console.

- [ ] **[Claude Code can help]** Assert trusted status via the GraphQL API:
  ```bash
  python scripts/validate-trust.py \
    --tenant your-tenant \
    --api-key $TWINGATE_API_KEY \
    --serial "YOUR-SERIAL-HERE" \
    --expected trusted
  ```
  > Verify by: script prints `PASS`.

---

## Phase 7: Reset and Re-test (optional)

- [ ] **[You do this]** Remove trust from the device in the Admin Console: Devices > click device > Actions > **Remove Trust**.
  > Verify by: device shows Trust Status: Untrusted again.

- [ ] **[You do this]** Wait for the next sync cycle (or restart the bridge). Confirm trust is re-applied.
  > Verify by: logs show `"action": "trusted"` again; Admin Console shows Trusted.

---

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `"action": "no_match"` in logs | Serial in Twingate doesn't match provider | Run `dump-serial.ps1` on VM; compare with provider UI; check for leading/trailing spaces |
| Device not appearing in Twingate | Twingate client not authenticated or no resource access | Re-authenticate client; ensure test user has access to a resource |
| `"action": "skipped"` in logs | Device is not marked compliant in the provider | Check compliance/patch status in provider UI |
| Provider auth error in logs | Wrong or expired API credentials | Regenerate credentials; update environment variable |
| No Twingate devices fetched | API key lacks Devices Read scope | Regenerate API key with correct scopes |
| `"action": "would_trust"` but never `"trusted"` | `dry_run: true` is still set | Change to `dry_run: false` and restart |
| Bridge exits immediately | Config parse error (missing required field or bad `${VAR}`) | Check logs for `config_error` event; verify all env vars are set |
