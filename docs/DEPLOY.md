# Deploying the demo to Azure

The public demo runs on **one small Ubuntu VM** with the same Docker Compose stack as
`make demo`, restored from `demo_snapshot/`. **Caddy** is the only public entry and serves
HTTPS on a free Azure name such as `https://txlens-1a2b3c.northcentralus.cloudapp.azure.com`. The
decision and its alternatives are in [ADR 0021](adr/0021-azure-vm-demo-deploy.md).

- Synthetic data only. No OpenAI key on the server: LLM answers come from the cache, so
  nobody can spend money through the demo.
- Public: ports 80/443 only. SSH (22) is open only to your current IP. Postgres, Temporal,
  the API and Next.js listen on the VM's 127.0.0.1. Langfuse, the MLflow UI and Spark do not
  run in the cloud.
- Secrets and demo passwords are generated on the server, differ from your local `.env`, are
  never printed and never committed. The demo passwords go into your macOS Keychain.

## What it costs (Azure list prices, North Central US, Linux, November 2026)

| Item | Price | Always on (730 h) | Around interviews (~20 h/month) |
|---|---|---|---|
| VM Standard_B2als_v2 (2 vCPU, 4 GB) | $0.0376/h | $27.45 | $0.75 |
| OS disk, Standard SSD E6 64 GB (+ operations) | $4.80/month + ~$0.30 | $5.10 | $5.10 |
| Standard static public IP (bills even when the VM is off) | $0.005/h | $3.65 | $3.65 |
| Egress (first 100 GB/month free), NSG, VNet, auto-shutdown, budget | free | $0 | $0 |
| **Total per month** | | **≈ $36** ($100 lasts ~2.7 months) | **≈ $9.50** ($100 lasts ~10 months) |

- Forgot `make cloud-stop`? The VM shuts down every night at 23:00 Central anyway. A month of
  ~14 h/day costs ≈ $25, and the $20 budget alert e-mails you before that.
- When the student credit runs out, Azure disables the subscription. It never bills a card.
- Prices: <https://prices.azure.com/api/retail/prices> (the Azure Retail Prices API).

## One-time setup (on your Mac)

1. Azure CLI: `brew install --cask azure-cli`, and `shellcheck` for `make cloud-lint`.
2. Log in yourself: `az login`, then pick the "Azure for Students" subscription
   (`az account set --subscription "<name or id>"`).
3. A dedicated SSH key **with a passphrase** (stored in the Keychain):
   ```
   ssh-keygen -t ed25519 -f ~/.ssh/txlens_azure -C txlens-azure
   ssh-add --apple-use-keychain ~/.ssh/txlens_azure
   ```
4. A demo snapshot: `make demo-snapshot` (the deploy uploads `demo_snapshot/`, 721 MB).
5. Commit your work: the deploy uploads `git archive HEAD`, never uncommitted files.

Settings (environment variables, all optional):

| Variable | Default |
|---|---|
| `TXLENS_AZ_LOCATION` | `northcentralus` |
| `TXLENS_AZ_VM_SIZE` | `Standard_B2als_v2` |
| `TXLENS_AZ_RG` | `txlens-demo-rg` |
| `TXLENS_AZ_SSH_KEY` | `~/.ssh/txlens_azure` |
| `TXLENS_AZ_BUDGET` | `20` (dollars per month) |

## Deploy

```
make cloud-deploy     # ~30-45 min the first time (image builds + 721 MB upload + restore)
make cloud-budget     # once: $20/month budget, e-mail alerts at 50/80/100% and on forecast
make cloud-stop       # when you are done
```

`make cloud-deploy` is idempotent. A second run only applies what changed: Bicep makes no
changes, the snapshot is skipped if the VM already restored it, and existing secrets are kept.
It does this, in order:

1. **Azure resources (Bicep, `infra/azure/main.bicep`)**, all tagged:
   - the resource group;
   - the NSG (22 from your IP /32, 80 and 443 from the Internet);
   - the VNet;
   - a Standard static IP with the DNS label `txlens-<6 hex of your subscription id>`;
   - the VM (Ubuntu 24.04 LTS, SSH key only, Trusted Launch, Standard SSD 64 GB);
   - the nightly auto-shutdown.
2. **Pins the VM's SSH host key**, read through the Azure API (`az vm run-command`), into
   `~/.ssh/known_hosts_txlens_azure`. The first SSH connection is already verified.
3. **Host setup** (`infra/azure/remote/host-setup.sh`, re-runnable):
   - Docker Engine and the compose plugin, with rotating container logs;
   - ufw (22/80/443 only);
   - SSH with keys only, no root and only the `txlens` user;
   - unattended security updates (no automatic reboot);
   - a 4 GB swap file.
4. **Code**: `git archive HEAD | ssh …`, then synced into `/opt/txlens` on the VM. No GitHub
   credentials or deploy key are on the VM.
5. **Snapshot**: `scp` into `/opt/txlens/demo_snapshot/`. Checksums are verified by the
   restore. The upload is deleted after a successful restore.
6. **Install** (`infra/azure/remote/install.sh`):
   - writes `.env` with new random secrets (`gen_env.py`, mode 600);
   - runs `infra/demo/restore.sh` with the cloud compose override, which also moves MLflow's
     paths to the new folder;
   - new demo passwords (`txlens-seed-users`);
   - `txlens-demo reset-sessions`: deletes every login attempt and refresh token from your
     laptop, with an audit row per center;
   - no Langfuse database, and the test-only database role cannot log in;
   - surveillance is pinned to the data cut-off. `DATA_CUTOFF` is the extract date from
     `data/raw/extract.json` (2026-09-30). Runs dated after it are deleted
     (`txlens-demo drop-surveillance-after`, audited), and the worklist is rebuilt as of the
     cut-off. A later date is refused by the CLI and by the worker, and a nightly schedule
     created with `DATA_CUTOFF` set runs as of the cut-off. The VM has no schedule;
   - starts the stack with Caddy.
7. **Passwords → Keychain** (`make cloud-passwords`): items `txlens-azure` / `clinician`,
   `researcher`, `data_steward`, `admin`. Log in as `<role>.center_a` (or `.center_b`, `.center_c`).
   To see one: Keychain Access → search "txlens-azure", or
   `security find-generic-password -s txlens-azure -a clinician -w`.
8. **Smoke test** (below).

## Day to day

| Command | What it does |
|---|---|
| `make cloud-start` | Start the VM, allow SSH from your current IP, wait until HTTPS answers |
| `make cloud-stop` | Deallocate: compute billing stops (disk + IP ≈ $9/month remain) |
| `make cloud-status` | Power state, URL, SSH source IP, memory/disk, pending reboot |
| `make cloud-smoke` | The smoke test |
| `make cloud-update` | Upload the current commit, rebuild, restart (data untouched) |
| `make cloud-ssh` | A shell on the VM (`cd /opt/txlens`) |
| `make cloud-tunnel` | Temporal UI on <http://localhost:18233> through SSH |
| `make cloud-passwords` | Copy the demo and guest passwords into the Keychain again |
| `make cloud-guest-rotate DAYS=30` | New guest password, open for DAYS days; Keychain updated, guest sessions end |
| `make cloud-reset` | Demo data back to the snapshot (users, guest accounts and audit trail kept; ~10 min) |
| `make cloud-share-on DAYS=21` | Keep the site up while a link is out: nightly shutdown off, VM running, cost estimate |
| `make cloud-share-off` | Nightly shutdown on again |
| `make cloud-screenshots` | README screenshots from the live demo (`MODE=copilot` for the copilot ones) |
| `make cloud-destroy` | Delete everything (type the resource group name to confirm) |

Before an interview: `make cloud-start` (2-3 minutes), open the URL, log in. Afterwards:
`make cloud-stop`.

New demo data: `make demo-snapshot` locally, then `make cloud-deploy`. The snapshot has a new
checksum, so it is uploaded and restored again.

## Sharing the demo with guests

- **Guest accounts:** `guest.clinician` (clinician) and `guest.researcher` (researcher), both
  in center A, share one password.
  - The password is made on the VM and stored in your Keychain (service `txlens-azure`,
    account `guest`).
  - To copy it: `security find-generic-password -s txlens-azure -a guest -w | pbcopy`.
  - Send it on request; it is never in the repo or on the page.
- **Expiry:** guest access ends at `GUEST_EXPIRES_AT`, 30 days after the first deploy
  (`GUEST_DAYS`). After that, login and token refresh are refused, and a guest with the right
  password reads "Guest access has ended; ask for new credentials". Other accounts keep the
  generic message.
  - A new password and window: `make cloud-guest-rotate DAYS=30`.
- **What guests can do:** the normal role permissions and rate limits apply, and every action
  is audited.
  - Guests can acknowledge, resolve and approve, which changes the demo data.
  - `make cloud-reset` brings the data back and keeps the users and the audit trail. The
    snapshot stays on the VM for this, about 0.7 GB.
- **Per-session copilot history:** all visitors share the two guest accounts, so a guest sees
  only the copilot conversations started in its own browser session (each guest login gets
  its own session id in the token; other accounts keep their full history).
  - Saved cohorts are still shared between guests.
  - `make cloud-reset` deletes the guests' conversations and saved cohorts
    (`txlens-demo clear-guest-work`; audit rows stay).
- **Demo guide:** the login page shows which guest account opens which view, when guest access
  ends, and three things to try. It appears only when `DEMO_GUIDE=1` (set on the VM).
- **Copilot without a key:** the six suggested questions (`backend/src/txlens_backend/copilot/suggested.py`)
  are answered from the LLM cache.
  - Any other question gets "Live AI is disabled in this public demo; try one of the
    suggested questions."
  - To change them, edit the list, then fill the cache locally with your key
    (`uv run txlens-demo copilot-cache`, a fraction of a cent).
  - Check offline (`uv run txlens-demo copilot-cache --offline`), then run
    `make demo-snapshot` and `make cloud-reset`.
- **Keep it up while the link is out:** `make cloud-share-on DAYS=21` turns the nightly
  shutdown off and prints the cost (21 days ≈ $25 at list prices).
  - `make cloud-share-off` turns the shutdown back on.
  - `make cloud-status` shows whether sharing is on and until when.

## Smoke test (`make cloud-smoke`, `infra/azure/smoke.sh`)

1. `https://<name>/login` answers 200 with a valid certificate. `http://` redirects to
   `https://`. HSTS is on pages and on `/api`.
2. `X-Robots-Tag: noindex` is set, `/robots.txt` disallows everything, and the
   synthetic-data banner is on the page.
3. Ports 3000, 3001, 5001, 5432, 7233, 8000 and 8233 are closed from the Internet.
4. `clinician.center_a` logs in (password from the Keychain, never printed). The cookies are
   `Secure` and `HttpOnly`.
5. Silas Marmere is found by name, and his timeline and patient page load.
6. There is no OpenAI key in the server's `.env` or in the API or worker containers, and no
   Langfuse database. Right after a deploy (`--fresh`), there are also no login attempts or
   refresh tokens from the laptop.
7. Surveillance is pinned: `DATA_CUTOFF` is set and the worker sees it, no run is dated after
   the cut-off, and the latest run is on the cut-off.
8. The demo guide is on, guest access is open, both guest accounts log in with their roles,
   and their expiry in the database matches `.env`.
9. The copilot's 6 suggested questions are answered from the cache, without an API key.

## Troubleshooting

- **"Missed test" flags for dates after the data ends, or every flag marked "older data":**
  a surveillance run was dated after the data cut-off. This happens, for example, when a
  laptop's nightly schedule ran before `make demo-snapshot`. `make cloud-update` removes such
  runs and rebuilds the worklist as of the cut-off.
  - On a laptop, set `DATA_CUTOFF=2026-09-30` in `.env`, then run
    `make surveillance-schedule` to pin the schedule.
  - Or pause it: `docker compose -p txlens -f infra/docker-compose.yml --env-file .env exec temporal temporal
    schedule toggle --schedule-id surveillance-nightly --pause --reason demo`.

- **SSH times out** after a network change: `make cloud-start` (or `make cloud-status`, then
  `make cloud-start`) moves the SSH rule to your current IP.
- **Policy or quota errors on deploy:** the student subscription allows only some regions
  (in November 2026: northcentralus, canadacentral, swedencentral, francecentral, denmarkeast;
  see `az policy assignment list`). North Central US is the cheapest of them and the default.
  B2s and B2ls_v2 are not offered there. Try another allowed region with
  `TXLENS_AZ_LOCATION=swedencentral make cloud-deploy`. Check with
  `az vm list-skus -l <region> --size Standard_B2` and `az vm list-usage -l <region>`.
- **No certificate yet:** Caddy needs ports 80/443 reachable and the DNS name resolving.
  Check `make cloud-ssh`, then `cd /opt/txlens && docker compose -p txlens -f infra/docker-compose.yml -f infra/azure/compose.cloud.yml --env-file .env logs caddy`.
  Certificates are kept in the `caddy-data` volume, so restarts do not request new ones.
- **Budget refused:** some student offers do not support Cost Management budgets. Watch the
  remaining credit in the Azure portal instead (search "Education", then Overview). The
  nightly shutdown still limits the damage.
- **Reboot required** (shown by `make cloud-status` after a kernel update): `make cloud-ssh`,
  then `sudo reboot`. The stack restarts by itself (`restart: unless-stopped`).
- **Out of memory** (`make cloud-status` shows little free memory and swap in use):
  `TXLENS_AZ_VM_SIZE=Standard_B2as_v2 make cloud-deploy` (8 GB). Bicep resizes the VM.
