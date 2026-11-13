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
#   passwords  copy the demo passwords from the VM into the macOS Keychain
#   update     upload the current commit, rebuild and restart (data untouched)
#   smoke      the smoke test (infra/azure/smoke.sh)
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
  echo "    https://$HOST"

  pin_host_key
  step "Host setup (Docker, firewall, SSH hardening, unattended upgrades, swap)"
  ssh_vm "sudo bash -s -- $ADMIN" < infra/azure/remote/host-setup.sh

  push_code
  push_snapshot
  step "Install on the VM"
  ssh_vm "bash $REMOTE/infra/azure/remote/install.sh $HOST"

  passwords
  bash infra/azure/smoke.sh --fresh
  step "Done: https://$HOST  (make cloud-stop when you are finished: the VM costs money)"
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
  printf '%s %s\n' "$HOST" "$key" > "$KNOWN_HOSTS"
  for _ in $(seq 1 30); do
    ssh_vm true 2>/dev/null && return
    sleep 5
  done
  fail "SSH to $HOST does not answer"
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
      --exclude=/.restored-manifest.json $REMOTE/.release/ $REMOTE/
    rm -rf $REMOTE/.release"
}

# The demo snapshot, over SSH (scp), unless the VM already restored this exact one.
push_snapshot() {
  [[ -f demo_snapshot/manifest.json ]] || fail "no demo_snapshot/ (run make demo-snapshot)"
  local local_sum remote_sum
  local_sum=$(shasum -a 256 demo_snapshot/manifest.json | cut -d' ' -f1)
  remote_sum=$(ssh_vm "sha256sum $REMOTE/.restored-manifest.json 2>/dev/null | cut -d' ' -f1" || true)
  if [[ "$local_sum" == "$remote_sum" ]]; then
    echo "    the VM already has this snapshot"
    return
  fi
  step "Uploading the demo snapshot ($(du -sh demo_snapshot | cut -f1), checksums verified on restore)"
  ssh_vm "mkdir -p $REMOTE/demo_snapshot"
  scp_vm demo_snapshot/manifest.json demo_snapshot/roles.sql demo_snapshot/txlens.dump \
    demo_snapshot/mlruns.tgz demo_snapshot/data_raw.tgz "$ADMIN@$HOST:$REMOTE/demo_snapshot/"
}

start() {
  need_az
  step "Starting $VM"
  az vm start -g "$RG" -n "$VM" -o none
  allow_my_ip
  resolve_host
  step "Waiting for https://$HOST"
  for _ in $(seq 1 60); do
    if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://$HOST/login")" == 200 ]]; then
      echo "    up: https://$HOST  (make cloud-stop afterwards)"
      return
    fi
    sleep 5
  done
  fail "https://$HOST did not answer within 5 minutes (make cloud-status, make cloud-ssh)"
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
  echo "    https://$HOST"
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

# Each demo password goes from the VM's .env into the Keychain through `security -i` on stdin,
# so it never appears in a process list, a file or the terminal.
passwords() {
  resolve_host
  step "Demo passwords -> macOS Keychain (service \"$KEYCHAIN\")"
  local role var value
  for role in $ROLES; do
    var="DEMO_$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')_PASSWORD"
    value=$(ssh_vm "grep -m1 '^$var=' $REMOTE/.env | cut -d= -f2-")
    [[ -n "$value" ]] || fail "$var is missing on the VM"
    printf 'add-generic-password -U -s %s -a %s -l %s-%s -w %s\n' \
      "$KEYCHAIN" "$role" "$KEYCHAIN" "$role" "$value" | security -i >/dev/null
    security find-generic-password -s "$KEYCHAIN" -a "$role" >/dev/null 2>&1 \
      || fail "could not store the $role password in the Keychain"
    echo "    $role: stored (log in as $role.center_a, .center_b or .center_c)"
  done
}

update() {
  need_az
  need_key
  resolve_host
  push_code
  step "Rebuild and restart on the VM"
  ssh_vm "bash $REMOTE/infra/azure/remote/install.sh $HOST"
  bash infra/azure/smoke.sh
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
  for role in $ROLES; do
    security delete-generic-password -s "$KEYCHAIN" -a "$role" >/dev/null 2>&1 || true
  done
  echo "    deleted; Keychain items and the pinned host key removed"
}

case "${1:-}" in
  deploy | start | stop | status | tunnel | passwords | update | budget | destroy) "$1" ;;
  ssh) ssh_vm ;;
  smoke) bash infra/azure/smoke.sh ;;
  *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
