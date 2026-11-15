#!/usr/bin/env bash
# The nightly demo reset on the VM. A systemd timer (txlens-reset.timer, 03:00 US Central)
# runs reset.sh, the same as `make cloud-reset`: demo data back to the snapshot, users and the
# audit trail kept. Only while sharing mode is on. Run as the admin user:
#   reset-timer.sh install <domain>          write/refresh the units (install.sh); on/off kept
#   reset-timer.sh on <YYYY-MM-DD>           reset nightly until that day (make cloud-share-on)
#   reset-timer.sh off                       no nightly resets (make cloud-share-off)
#   reset-timer.sh status                    sharing, last and next reset, recent log lines
#   reset-timer.sh facts                     for the smoke test: "reset_until", "reset_timer_enabled"
#   reset-timer.sh run <domain> [--dry-run]  what the timer starts; --dry-run changes nothing
# Each run adds one line to the log; a reset's full output is in `journalctl -u txlens-reset`.
set -euo pipefail
cd "$(dirname "$0")/../../.."
STATE="${TXLENS_RESET_STATE:-/var/lib/txlens/sharing-until}"
LOG="${TXLENS_RESET_LOG:-/var/log/txlens/nightly-reset.log}"
SNAPSHOT="${TXLENS_RESET_SNAPSHOT:-demo_snapshot/manifest.json}"
UNITS=/etc/systemd/system
TIMER=txlens-reset.timer
ZONE=America/Chicago

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }
today() { echo "${TXLENS_TODAY:-$(TZ=$ZONE date +%F)}"; }
central() { TZ=$ZONE date -d "$1" '+%a %d %b %Y, %H:%M %Z'; }

# The last day of sharing (US Central), or nothing when sharing is off or over.
sharing_until() {
  local until
  until=$(cat "$STATE" 2>/dev/null || true)
  if [[ "$until" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && ! "$until" < "$(today)" ]]; then
    echo "$until"
  fi
}

# The timer runs exactly while sharing is on.
apply() {
  if [[ -n "$(sharing_until)" ]]; then
    sudo systemctl enable --now --quiet "$TIMER"
  else
    sudo systemctl disable --now --quiet "$TIMER" 2>/dev/null || true
  fi
}

install_units() {
  local domain="$1" admin unit tmp changed=0
  admin=$(id -un)
  sudo mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"
  sudo chown "$admin:$admin" "$(dirname "$STATE")" "$(dirname "$LOG")"
  tmp=$(mktemp)
  for unit in txlens-reset.service txlens-reset.timer; do
    sed -e "s/@ADMIN@/$admin/g" -e "s/@DOMAIN@/$domain/g" "infra/azure/remote/$unit" > "$tmp"
    if ! cmp -s "$tmp" "$UNITS/$unit"; then
      sudo install -m 644 "$tmp" "$UNITS/$unit"
      changed=1
    fi
  done
  rm -f "$tmp"
  if [[ "$changed" == 1 ]]; then sudo systemctl daemon-reload; fi
  apply
  echo "    nightly reset: $(describe)"
}

describe() {
  local until
  until=$(sharing_until)
  if [[ -n "$until" ]]; then echo "on, 03:00 US Central, until $until"; else echo "off (sharing mode is off)"; fi
}

status() {
  local next last
  echo "    nightly reset: $(describe); timer $(systemctl is-enabled "$TIMER" 2>/dev/null || echo missing)"
  next=$(systemctl show "$TIMER" -p NextElapseUSecRealtime --value 2>/dev/null || true)
  if [[ -n "$next" && "$next" != n/a ]]; then echo "    next reset: $(central "$next")"; else echo "    next reset: none"; fi
  last=$(grep -E ' (done|FAILED)' "$LOG" 2>/dev/null | tail -1 || true)
  if [[ -n "$last" ]]; then
    echo "    last reset: $(central "${last%% *}"): ${last#* }"
  else
    echo "    last reset: none yet"
  fi
  if [[ -s "$LOG" ]]; then
    echo "    log ($LOG, newest last):"
    tail -5 "$LOG" | sed 's/^/      /'
  fi
}

run() {
  local domain="$1" dry="${2:-}" until start code
  until=$(sharing_until)
  if [[ -z "$until" ]]; then
    if [[ "$dry" == --dry-run ]]; then
      log "dry run: sharing mode is off, the reset would be skipped"
      return 0
    fi
    log "skipped: sharing mode is off; nightly timer turned off"
    apply
    return 0
  fi
  if [[ ! -f "$SNAPSHOT" ]]; then
    log "FAILED: no demo snapshot on the VM ($SNAPSHOT)"
    return 1
  fi
  if [[ "$dry" == --dry-run ]]; then
    log "dry run: would reset $domain now (sharing until $until); snapshot present"
    if command -v systemd-analyze >/dev/null && [[ -f "$UNITS/$TIMER" ]]; then
      systemd-analyze verify "$UNITS/txlens-reset.service" "$UNITS/$TIMER"
      systemd-analyze calendar "*-*-* 03:00:00 $ZONE" | sed -n 's/^ *Next elapse: /    next 03:00 Central: /p'
    fi
    return 0
  fi
  log "reset started (sharing until $until)"
  start=$SECONDS
  if bash infra/azure/remote/reset.sh "$domain"; then
    for _ in $(seq 1 30); do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$domain/login" || true)
      [[ "$code" == 200 ]] && break
      sleep 10
    done
    log "done in $(( (SECONDS - start) / 60 )) min; https://$domain/login answers $code"
  else
    code=$?
    log "FAILED (exit $code) after $(( (SECONDS - start) / 60 )) min; details: journalctl -u txlens-reset"
    return "$code"
  fi
}

case "${1:-}" in
  install) install_units "$2" ;;
  on)
    [[ "${2:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "usage: $0 on YYYY-MM-DD" >&2; exit 2; }
    echo "$2" > "$STATE"
    apply
    echo "    nightly reset: $(describe)"
    ;;
  off)
    rm -f "$STATE"
    apply
    echo "    nightly reset: $(describe)"
    ;;
  status) status ;;
  facts)
    echo "reset_until $(sharing_until | grep . || echo none)"
    echo "reset_timer_enabled $([[ "$(systemctl is-enabled "$TIMER" 2>/dev/null)" == enabled ]] && echo 1 || echo 0)"
    ;;
  run) run "$2" "${3:-}" ;;
  *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
