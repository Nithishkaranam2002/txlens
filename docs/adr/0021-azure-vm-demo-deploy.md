# ADR 0021: The public demo on one small Azure VM, behind Caddy

**Status:** accepted (2026-11-13)

## Context
The demo ran only on a laptop (ADR 0020). For interviews it needs a public HTTPS address. The
budget is an "Azure for Students" credit of $100 that has to last months. The demo shows
synthetic data only, must not let a stranger spend money on LLM calls, and should be up only
when it is being shown.

## Options compared

| | **One VM + compose + Caddy** (chosen) | Azure Container Apps | App Service + Azure Database for PostgreSQL |
|---|---|---|---|
| Runs our compose stack as is | Yes | No: Postgres, Temporal and the worker need rework | No: Temporal and the worker need rework |
| Postgres with pgvector, RLS, owner-kept restore | Our container, restored from the snapshot | Needs a managed database | Managed (extra ~$13+/month, even idle) |
| Cost per month: always on / around interviews | ~$36 / ~$9.50 | Scale-to-zero app, but a database or a VM is still needed | ~$25+ / ~$15+ (database keeps billing) |
| Stop completely when not demoing | Deallocate: only disk + IP | Partly | Database billed unless stopped (auto-restarts after 7 days) |
| Things to secure ourselves | OS patches, firewall, SSH | Few | Few |

## Decision
- **One Ubuntu 24.04 LTS VM** (Standard_B2als_v2: 2 vCPU, 4 GB, plus a 4 GB swap file;
  Standard SSD 64 GB), Trusted Launch, in one resource group. Every resource is tagged
  (`project`, `env`, `owner`, `purpose`, `data=synthetic-only`).
- **Infrastructure as code: Bicep** (`infra/azure/main.bicep`), deployed by
  `infra/azure/cloud.sh` through the Azure CLI. Bicep is declarative, so a second run changes
  nothing. Host setup (Docker, ufw, SSH hardening, unattended upgrades, swap) is a re-runnable
  script over SSH instead of cloud-init, because Azure does not let you change a VM's
  cloud-init data after it is created.
- **The existing compose stack** plus `infra/azure/compose.cloud.yml`. Caddy is the only
  service with public ports (80/443). It gets automatic HTTPS on the free Azure DNS name
  (`<label>.<region>.cloudapp.azure.com`) and sets HSTS, `X-Robots-Tag: noindex` and a
  disallow-all `robots.txt`. Every other port stays on the VM's 127.0.0.1. The NSG allows only
  22 (from the owner's current IP), 80 and 443; ufw does the same on the VM. Temporal UI is
  reached through an SSH tunnel. Langfuse, the MLflow UI and Spark do not run in the cloud.
- **Data comes from the demo snapshot** (`scp`, then `infra/demo/restore.sh`), with no
  pipelines in the cloud. After the restore the cloud install:
  - generates new demo passwords;
  - deletes every login attempt and refresh token from the source machine, with an audit row
    per center;
  - creates no Langfuse database;
  - blocks the test-only login role.
  MLflow's absolute paths are rewritten to the new folder (`infra/demo/relocate_mlruns.py`),
  so a snapshot now restores into any checkout, not only the same path.
  Surveillance is pinned to the data cut-off (`DATA_CUTOFF`, the extract date). Runs dated
  after it are deleted and the worklist is rebuilt as of the cut-off. Later dates are refused
  by the CLI and by the worker's activity, so even a stray schedule cannot invent "missed
  test" flags.
- **Secrets are made on the server** (`infra/azure/remote/gen_env.py`), are different from the
  laptop's, are never printed and stay in a mode-600 `.env`. The demo passwords go from the VM
  straight into the macOS Keychain (`security -i` on stdin), never to the terminal.
- **No OpenAI key on the server.** The compose override forces `OPENAI_API_KEY` to empty in
  every backend container, so the copilot and the AI steps answer from the LLM cache only.
- **Code transfer without GitHub credentials:** `git archive HEAD` is piped over SSH. The VM's
  SSH host key is read through the Azure API (`az vm run-command`) and pinned before the first
  connection, so there is no trust-on-first-use.
- **Cost control:**
  - an auto-shutdown schedule every night at 23:00 Central;
  - `make cloud-stop` deallocates the VM;
  - `make cloud-start` starts it and moves the SSH rule to the current IP;
  - a $20/month subscription budget with e-mail alerts at 50/80/100% and on forecast;
  - `make cloud-destroy` deletes the resource group.

## Consequences
- Idle cost is the disk (~$5) and the static IP (~$3.65): Basic public IPs are retired, and a
  Standard IP bills while the VM is off. Releasing it on every stop would save that, but was
  turned down for simplicity and a reserved hostname.
- We patch the OS ourselves (unattended security updates, no automatic reboot during a demo;
  `make cloud-status` reports a pending reboot).
- Caddy replaces any `X-Forwarded-For` sent by the client, so behind it the per-address login
  limit sees the real client address (the gap ADR 0011 notes for direct access to Next.js).
- The snapshot contains the licensed OMOP vocabulary. It goes only to a private VM that only
  the owner controls, the same as the laptop. The demo shows concept names, not the files.
- A bigger VM (8 GB, ~$63/month always on) is one parameter away if 4 GB proves tight.
- Moving to Container Apps later would need a managed Postgres with pgvector and a hosted
  Temporal. The images are already non-root with a read-only root filesystem.
