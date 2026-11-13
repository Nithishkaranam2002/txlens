# shellcheck shell=bash disable=SC2034  # settings used by the scripts that source this file
# Shared settings and helpers of infra/azure/cloud.sh and smoke.sh (sourced, not run).
# Runs on the Mac. Secrets never pass through arguments or stdout: passwords go from the VM
# straight into the macOS Keychain, and are read back only into shell variables.

RG="${TXLENS_AZ_RG:-txlens-demo-rg}"
LOCATION="${TXLENS_AZ_LOCATION:-northcentralus}"
VM_SIZE="${TXLENS_AZ_VM_SIZE:-Standard_B2als_v2}"
SSH_KEY="${TXLENS_AZ_SSH_KEY:-$HOME/.ssh/txlens_azure}"
KNOWN_HOSTS="$HOME/.ssh/known_hosts_txlens_azure"
ADMIN=txlens
VM=txlens-demo-vm
NSG=txlens-demo-nsg
PUBLIC_IP=txlens-demo-ip
BUDGET=txlens-monthly
KEYCHAIN=txlens-azure
ROLES="clinician researcher data_steward admin"
SHUTDOWN="shutdown-computevm-$VM"
# Azure list prices, North Central US, Linux (Retail Prices API, 2026-11-13), for estimates.
PRICE_VM_HOUR=0.0376      # Standard_B2als_v2
PRICE_DISK_MONTH=4.80     # Standard SSD E6, 64 GB
PRICE_IP_HOUR=0.005       # Standard static public IPv4

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*" >&2; }
fail() { printf '\ncloud: %s\n' "$*" >&2; exit 1; }

need_az() {
  command -v az >/dev/null || fail "the Azure CLI is missing: brew install --cask azure-cli"
  az account show -o none 2>/dev/null || fail "not logged in to Azure: run az login"
}

need_key() {
  [[ -f "$SSH_KEY" && -f "$SSH_KEY.pub" ]] || fail "no SSH key at $SSH_KEY. Create one (choose a
       passphrase): ssh-keygen -t ed25519 -f $SSH_KEY -C txlens-azure
       then: ssh-add --apple-use-keychain $SSH_KEY"
}

# This Mac's public IPv4 address (the only source allowed to reach SSH).
my_ip() {
  local ip
  ip=$(curl -fsS --max-time 10 https://api.ipify.org) || fail "could not find this Mac's public IP"
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "unexpected public IP answer"
  printf '%s' "$ip"
}

# A DNS label that stays the same for this subscription, without any state file.
dns_label() {
  printf 'txlens-%s' "$(az account show --query id -o tsv | shasum -a 256 | cut -c1-6)"
}

# Sets VM_HOST to the VM's public name, e.g. txlens-1a2b3c.northcentralus.cloudapp.azure.com (once per run;
# call it directly, not in $(...), so the value stays set).
resolve_host() {
  [[ -n "${VM_HOST:-}" ]] && return
  VM_HOST=$(az network public-ip show -g "$RG" -n "$PUBLIC_IP" --query dnsSettings.fqdn -o tsv \
    2>/dev/null) || true
  [[ -n "$VM_HOST" ]] || fail "no deployment found in $RG (run make cloud-deploy)"
}

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o UserKnownHostsFile="$KNOWN_HOSTS"
  -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ConnectTimeout=10)

ssh_vm() { resolve_host; ssh "${SSH_OPTS[@]}" "$ADMIN@$VM_HOST" "$@"; }
scp_vm() { scp -q "${SSH_OPTS[@]}" "$@"; }

power_state() {
  az vm get-instance-view -g "$RG" -n "$VM" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" -o tsv
}

keychain_password() {
  security find-generic-password -s "$KEYCHAIN" -a "$1" -w 2>/dev/null
}
