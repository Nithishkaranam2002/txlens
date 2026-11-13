#!/usr/bin/env bash
# `make cloud-guest-rotate`, on the VM: a new guest password, valid for DAYS days from now.
#   bash /opt/txlens/infra/azure/remote/guest-rotate.sh <domain> <days>
# The API and worker restart to read the new expiry; guest sessions end. Never prints a secret.
set -euo pipefail
DOMAIN="$1" DAYS="$2"
cd "$(dirname "$0")/../../.."
export TXLENS_COMPOSE_OVERRIDE=infra/azure/compose.cloud.yml
# shellcheck source=../../demo/common.sh
source infra/demo/common.sh

python3 infra/azure/remote/gen_env.py "$PWD" "$DOMAIN" --rotate-guest "$DAYS"
tools txlens-seed-users --guests
compose --profile demo up -d --wait api worker  # .env changed: recreated with the new expiry
