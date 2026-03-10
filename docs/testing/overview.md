# End-to-End Testing Overview

This guide walks through validating the `twingate-device-trust-bridge` from first account creation through confirming a device is marked `isTrusted: true` in Twingate.

## What the test validates

The happy path: a Windows VM enrolled in at least one MDM/EDR provider is recognised by the bridge (matched by serial number), determined to be compliant, and marked trusted in Twingate via the `deviceUpdate` GraphQL mutation.

The specific assertions for a passing run:

1. The bridge logs `"action": "trusted"` (or `"action": "would_trust"` in dry-run mode) for the test device, with the correct `device_serial` and `twingate_device_id`.
2. The device shows **Trust Status: Trusted** in the Twingate Admin Console.
3. A Twingate GraphQL query for `isTrusted: true` returns the device.

## Scope

This guide covers the **happy path only**:

- Device enrolled in provider → serial matches → device is compliant → bridge trusts it.

The following are out of scope for this guide:

- Non-compliant device (should not be trusted)
- Serial mismatch (should produce `"action": "no_match"`)
- Provider API down during a sync cycle
- Multiple simultaneous providers with `trust.mode: all`

## Guide structure

| Document | Purpose |
|---|---|
| [credentials.md](credentials.md) | Obtaining free-trial / partner credentials for each of the 8 providers |
| [windows-vm-setup.md](windows-vm-setup.md) | Spinning up the test VM, enrolling in a provider, verifying the serial number |
| [twingate-setup.md](twingate-setup.md) | Creating a test Twingate network, generating an API key, confirming the device is untrusted |
| [local-run.md](local-run.md) | Running the bridge locally with `pip install -e ".[dev]"` |
| [docker-run.md](docker-run.md) | Running the bridge via the GHCR Docker image |
| [validation-checklist.md](validation-checklist.md) | Step-by-step checklist for the full end-to-end test |

Work through the documents in the order listed above, then use the checklist to verify each step.

## Prerequisites

Before starting, you need:

- A Windows 10 or 11 Pro/Enterprise VM (physical or virtual — see [windows-vm-setup.md](windows-vm-setup.md))
- An account with at least one supported provider (see [credentials.md](credentials.md))
- A Twingate account (free starter plan, up to 5 users — see [twingate-setup.md](twingate-setup.md))
- Either Python 3.12+ (for local run) or Docker Desktop (for Docker run)
- The bridge source code: `git clone https://github.com/twingate-solutions/twingate-mdm-connector.git`

## Where to start: testing a single provider first

Testing all 8 providers simultaneously requires 8 trial accounts and 8 agents installed on the VM. Start with a single provider to validate the full flow before expanding.

**Recommended starting providers:**

| Provider | Why |
|---|---|
| **JumpCloud** | Free tier (up to 10 devices, no expiry), simplest auth (single API key), no VM agent install — just run a one-line PowerShell command |
| **FleetDM** | Fully open-source, self-hosted via Docker, no account needed, free forever |

Both are described in detail in [credentials.md](credentials.md).

Skip **Datto RMM** (requires a sales-assisted partner demo request) and **Mosyle** (Apple devices only) unless you specifically need to test those providers.

Once the happy path is confirmed with one provider, add additional providers to `config.yaml` and re-run.

## Time estimate

| Scenario | Estimated time |
|---|---|
| First-time setup (one provider, new VM) | 2–3 hours |
| Adding a second provider to an existing setup | 20–30 minutes |
| Re-running the bridge after a config change | ~5 minutes |
| Full 8-provider validation from scratch | 6–8 hours |

Most of the first-run time is account sign-up, agent installation, and waiting for devices to appear in provider consoles. The bridge itself starts in under a second.
