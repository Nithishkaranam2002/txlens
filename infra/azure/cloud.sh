#!/usr/bin/env bash
# `make cloud-*`: the TxLens demo on one small Azure VM (docs/DEPLOY.md). Runs on the Mac and
# needs `az login` first. Commands:
#   deploy     create/update the Azure resources (Bicep), set up the VM, upload the code and the
#              demo snapshot, restore, start, store demo passwords in the Keychain, smoke test
#   start      start the VM, allow SSH from this Mac's current IP, wait for HTTPS
#   stop       deallocate the VM (then only the disk and the IP address cost money)
#   status     power state, URL, memory/disk and pending reboot
#   ssh        a shell on the VM
#   tunnel     Temporal UI of the VM on http://localhost:18233
#   passwords  copy the demo and guest passwords from the VM into the macOS Keychain
#   guest-rotate  new guest password, open for DAYS days (default 30); Keychain updated
#   reset      demo data back to the snapshot, keeping users and the audit trail
#   share-on   keep the site up for DAYS days (default 21): no nightly shutdown, VM running
#   share-off  nightly shutdown on again
#   update     upload the current commit, rebuild and restart (data untouched)
#   smoke      the smoke test (infra/azure/smoke.sh)
#   screenshots  README screenshots from the live demo (MODE=pages or copilot)
#   budget     monthly budget with e-mail alerts ($20 unless TXLENS_AZ_BUDGET is set)
#   destroy    delete the resource group, the budget, the pinned host key and Keychain items
# Settings: TXLENS_AZ_RG, TXLENS_AZ_LOCATION, TXLENS_AZ_VM_SIZE, TXLENS_AZ_SSH_KEY.
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=lib.sh
source infra/azure/lib.sh

REMOTE=/opt/txlens

deploy() {
  need_az
  need_key
  local ip label owner tags
  ip=$(my_ip)
  label=$(dns_label)
  owner=$(az account show --query user.name -o tsv)
  tags=$(python3 -c 'import json, sys; print(json.dumps({"project": "txlens", "env": "demo",
    "owner": sys.argv[1], "purpose": "interview-demo", "data": "synthetic-only"}))' "$owner")

  step "Resource group $RG in $LOCATION"
  az group create -n "$RG" -l "$LOCATION" --tags project=txlens env=demo owner="$owner" \
    purpose=interview-demo data=synthetic-only -o none

  step "Network, IP, VM ($VM_SIZE) and nightly shutdown (Bicep; SSH only from $ip)"
  az deployment group create -g "$RG" -n txlens-demo -f infra/azure/main.bicep -o none \
    -p location="$LOCATION" vmSize="$VM_SIZE" sshPublicKey="$(cat "$SSH_KEY.pub")" \
    sshSourceIp="$ip" dnsLabel="$label" tags="$tags"
  [[ "$(power_state)" == "VM running" ]] || az vm start -g "$RG" -n "$VM" -o none
  resolve_host
  echo "    https://$VM_HOST"

  pin_host_key
  step "Host setup (Docker, firewall, SSH hardening, unattended upgrades, swap)"
  ssh_vm "sudo bash -s -- $ADMIN" < infra/azure/remote/host-setup.sh

  push_code
  push_snapshot
  step "Install on the VM"
  ssh_vm "bash $REMOTE/infra/azure/remote/install.sh $VM_HOST"

  passwords
  bash infra/azure/smoke.sh --fresh
  step "Done: https://$VM_HOST  (make cloud-stop when you are finished: the VM costs money)"
}

# The VM's SSH host key, read through the Azure API (not over the network), so the first SSH
# connection is already verified.
pin_host_key() {
  step "Pinning the VM's SSH host key (read through the Azure API)"
  local key
  key=$(az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
    --scripts "cat /etc/ssh/ssh_host_ed25519_key.pub" --query "value[0].message" -o tsv \
    | grep -oE 'ssh-ed25519 [A-Za-z0-9+/=]+' | head -1) || true
  [[ -n "$key" ]] || fail "could not read the VM's host key"
  mkdir -p "$HOME/.ssh"
  printf '%s %s\n' "$VM_HOST" "$key" > "$KNOWN_HOSTS"
  for _ in $(seq 1 30); do
    ssh_vm true 2>/dev/null && return
    sleep 5
  done
  fail "SSH to $VM_HOST does not answer"
}

# The committed code (git archive of HEAD): no .env, data, model store or GitHub credentials.
push_code() {
  step "Uploading the code of $(git rev-parse --short HEAD)"
  [[ -z "$(git status --porcelain)" ]] || warn "uncommitted changes are not uploaded"
  local snapshot_commit
  snapshot_commit=$(python3 -c "import json; print(json.load(open('demo_snapshot/manifest.json'))['git_commit'])")
  [[ "$snapshot_commit" == "$(git rev-parse HEAD)" ]] \
    || warn "the snapshot was taken at ${snapshot_commit:0:7}; restore migrates it forward"
  git archive --format=tar.gz HEAD | ssh_vm "set -e
    rm -rf $REMOTE/.release && mkdir $REMOTE/.release && tar -xzf - -C $REMOTE/.release
    # First matching rule wins; excluded paths (secrets, data) are also kept by --delete.
    rsync -a --delete --include=/.env.example --exclude='/.env*' --exclude=/.release \
      --exclude=/data/ --exclude='/mlruns*' --exclude=/demo_snapshot/ \
      --exclude=/.restored-manifest.json --exclude=/.snapshot-audit-max --exclude=/.reset/ \
      $REMOTE/.release/ $REMOTE/
    rm -rf $REMOTE/.release"
}

# The demo snapshot, over SSH (scp), unless the VM already holds this exact one. It stays on
# the VM: install.sh restores it when it differs from the last one restored, and
# make cloud-reset restores it again.
push_snapshot() {
  [[ -f demo_snapshot/manifest.json ]] || fail "no demo_snapshot/ (run make demo-snapshot)"
  local local_sum remote_sum
  local_sum=$(shasum -a 256 demo_snapshot/manifest.json | cut -d' ' -f1)
  remote_sum=$(ssh_vm "sha256sum $REMOTE/demo_snapshot/manifest.json 2>/dev/null | cut -d' ' -f1" \
    || true)
  if [[ "$local_sum" == "$remote_sum" ]]; then
    echo "    the VM already has this snapshot"
    return
  fi
  step "Uploading the demo snapshot ($(du -sh demo_snapshot | cut -f1), checksums verified on restore)"
  # Docker creates a missing bind-mount folder as root (the tools service mounts this one).
  ssh_vm "mkdir -p $REMOTE/demo_snapshot && sudo chown $ADMIN:$ADMIN $REMOTE/demo_snapshot"
  scp_vm demo_snapshot/manifest.json demo_snapshot/roles.sql demo_snapshot/txlens.dump \
    demo_snapshot/mlruns.tgz demo_snapshot/data_raw.tgz "$ADMIN@$VM_HOST:$REMOTE/demo_snapshot/"
}

start() {
  need_az
  step "Starting $VM"
  az vm start -g "$RG" -n "$VM" -o none
  allow_my_ip
  resolve_host
  step "Waiting for https://$VM_HOST"
  for _ in $(seq 1 60); do
    if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://$VM_HOST/login")" == 200 ]]; then
      echo "    up: https://$VM_HOST  (make cloud-stop afterwards)"
      return
    fi
    sleep 5
  done
  fail "https://$VM_HOST did not answer within 5 minutes (make cloud-status, make cloud-ssh)"
}

# Home and travel networks change: SSH is opened only to the address this Mac has now.
allow_my_ip() {
  local ip
  ip=$(my_ip)
  az network nsg rule update -g "$RG" --nsg-name "$NSG" -n ssh-from-owner \
    --source-address-prefixes "$ip/32" -o none
  echo "    SSH allowed from $ip only"
}

stop() {
  need_az
  step "Deallocating $VM (compute billing stops; disk and IP keep costing ~\$9/month)"
  az vm deallocate -g "$RG" -n "$VM" -o none
  echo "    $(power_state)"
}

status() {
  need_az
  resolve_host
  local state
  state=$(power_state)
  echo "    $VM: $state"
  echo "    https://$VM_HOST"
  echo "    nightly shutdown: $(shutdown_status)$(sharing_note)"
  echo "    SSH allowed from: $(az network nsg rule show -g "$RG" --nsg-name "$NSG" \
    -n ssh-from-owner --query sourceAddressPrefix -o tsv)"
  [[ "$state" == "VM running" ]] || return 0
  ssh_vm "free -m | sed -n 2,3p; df -h / | tail -1
    [[ -f /var/run/reboot-required ]] && echo 'reboot required (security update): sudo reboot' || true"
}

tunnel() {
  echo "    Temporal UI: http://localhost:18233  (Ctrl-C closes the tunnel)"
  ssh_vm -N -L 18233:127.0.0.1:8233
}

# A password goes from the VM's .env into the Keychain through `security -i` on stdin, so it
# never appears in a process list, a file or the terminal.
store_password() {  # store_password <Keychain account> <.env variable>
  local value
  value=$(ssh_vm "grep -m1 '^$2=' $REMOTE/.env | cut -d= -f2-")
  [[ -n "$value" ]] || fail "$2 is missing on the VM"
  printf 'add-generic-password -U -s %s -a %s -l %s-%s -w %s\n' \
    "$KEYCHAIN" "$1" "$KEYCHAIN" "$1" "$value" | security -i >/dev/null
  security find-generic-password -s "$KEYCHAIN" -a "$1" >/dev/null 2>&1 \
    || fail "could not store the $1 password in the Keychain"
}

passwords() {
  resolve_host
  step "Demo and guest passwords -> macOS Keychain (service \"$KEYCHAIN\")"
  local role
  for role in $ROLES; do
    store_password "$role" "DEMO_$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')_PASSWORD"
    echo "    $role: stored (log in as $role.center_a, .center_b or .center_c)"
  done
  store_password guest GUEST_PASSWORD
  echo "    guest: stored (guest.clinician and guest.researcher; open until $(guest_until))"
}

guest_until() { ssh_vm "grep -m1 '^GUEST_EXPIRES_AT=' $REMOTE/.env | cut -d= -f2"; }

guest_rotate() {
  need_az
  resolve_host
  local days="${1:-30}"
  step "New guest password, open for $days days"
  ssh_vm "bash $REMOTE/infra/azure/remote/guest-rotate.sh $VM_HOST $days"
  store_password guest GUEST_PASSWORD
  echo "    guest: new password stored in the Keychain; open until $(guest_until)"
}

reset() {
  need_az
  need_key
  resolve_host
  push_code
  push_snapshot
  step "Restoring the demo data (users and audit trail kept; about 10 minutes)"
  ssh_vm "bash $REMOTE/infra/azure/remote/reset.sh $VM_HOST"
  bash infra/azure/smoke.sh
}

update() {
  need_az
  need_key
  resolve_host
  push_code
  step "Rebuild and restart on the VM"
  ssh_vm "bash $REMOTE/infra/azure/remote/install.sh $VM_HOST"
  passwords
  bash infra/azure/smoke.sh
}

# Passwords go from the Keychain into the script's environment only, never to the terminal.
screenshots() {
  resolve_host
  env BASE_URL="https://$VM_HOST" \
    DEMO_CLINICIAN_PASSWORD="$(keychain_password clinician)" \
    DEMO_RESEARCHER_PASSWORD="$(keychain_password researcher)" \
    DEMO_DATA_STEWARD_PASSWORD="$(keychain_password data_steward)" \
    DEMO_ADMIN_PASSWORD="$(keychain_password admin)" \
    pnpm --dir frontend screenshots "${1:-pages}"
}

shutdown_status() {
  az resource show -g "$RG" -n "$SHUTDOWN" --resource-type Microsoft.DevTestLab/schedules \
    --query properties.status -o tsv
}

sharing_note() {
  local until
  until=$(az vm show -g "$RG" -n "$VM" --query "tags.\"sharing-until\"" -o tsv 2>/dev/null)
  [[ -n "$until" ]] && printf ' (sharing until %s)' "$until"
  return 0
}

set_shutdown() {  # set_shutdown Enabled|Disabled
  az resource update -g "$RG" -n "$SHUTDOWN" --resource-type Microsoft.DevTestLab/schedules \
    --set properties.status="$1" -o none
}

# What `days` of sharing cost at list prices: the VM always on, plus the disk and the IP.
share_cost() {
  python3 -c 'import sys
days, vm, disk, ip = int(sys.argv[1]), *map(float, sys.argv[2:])
vm_cost, disk_cost, ip_cost = vm * days * 24, disk * days / 30, ip * days * 24
total = vm_cost + disk_cost + ip_cost
print(f"    {days} days always on: about ${total:.2f} (VM ${vm_cost:.2f} + disk ${disk_cost:.2f}"
      f" + IP ${ip_cost:.2f}; list prices)")' \
    "$1" "$PRICE_VM_HOUR" "$PRICE_DISK_MONTH" "$PRICE_IP_HOUR"
}

share_on() {
  need_az
  local days="${1:-21}" until
  until=$(python3 -c 'import datetime as d, sys
print(d.date.today() + d.timedelta(days=int(sys.argv[1])))' "$days")
  step "Sharing for $days days (until $until): nightly shutdown off, VM running"
  set_shutdown Disabled
  az vm update -g "$RG" -n "$VM" --set "tags.sharing-until=$until" -o none
  if [[ "$(power_state)" != "VM running" ]]; then
    start
  else
    resolve_host
    echo "    already running: https://$VM_HOST"
  fi
  share_cost "$days"
  echo "    nightly shutdown: $(shutdown_status). When you are done: make cloud-share-off"
}

share_off() {
  need_az
  step "Sharing over: nightly shutdown on again (23:00 Central)"
  set_shutdown Enabled
  az vm update -g "$RG" -n "$VM" --remove tags.sharing-until -o none
  echo "    nightly shutdown: $(shutdown_status). The VM runs until then (or make cloud-stop)."
}

budget() {
  need_az
  local amount email
  amount="${TXLENS_AZ_BUDGET:-20}"
  if az consumption budget show --budget-name "$BUDGET" -o none 2>/dev/null; then
    echo "    budget $BUDGET already exists"
    return
  fi
  email=$(az account show --query user.name -o tsv)
  step "Budget $BUDGET: \$$amount/month, e-mails to $email at 50/80/100% (and forecast 100%)"
  az deployment sub create -l "$LOCATION" -n txlens-budget -f infra/azure/budget.bicep -o none \
    -p amount="$amount" startDate="$(date -u +%Y-%m-01)" contactEmails="[\"$email\"]" \
    || fail "the budget was refused (some student offers do not support budgets); see docs/DEPLOY.md"
}

destroy() {
  need_az
  local answer
  printf 'This deletes the VM, its disk, IP and network (resource group %s).\n' "$RG"
  read -r -p "Type the resource group name to confirm: " answer
  [[ "$answer" == "$RG" ]] || fail "not confirmed; nothing deleted"
  step "Deleting $RG (a few minutes)"
  az group delete -n "$RG" --yes
  az consumption budget delete --budget-name "$BUDGET" 2>/dev/null || true
  rm -f "$KNOWN_HOSTS"
  local role
  for role in $ROLES guest; do
    security delete-generic-password -s "$KEYCHAIN" -a "$role" >/dev/null 2>&1 || true
  done
  echo "    deleted; Keychain items and the pinned host key removed"
}

case "${1:-}" in
  deploy | start | stop | status | tunnel | passwords | update | budget | destroy | reset) "$1" ;;
  guest-rotate) guest_rotate "${2:-30}" ;;
  share-on) share_on "${2:-21}" ;;
  share-off) share_off ;;
  ssh) ssh_vm ;;
  smoke) bash infra/azure/smoke.sh ;;
  screenshots) screenshots "${2:-pages}" ;;
  *) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
