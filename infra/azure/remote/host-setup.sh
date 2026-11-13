#!/usr/bin/env bash
# Runs on the VM as root (`sudo bash host-setup.sh <admin user>`), from `make cloud-deploy`.
# Idempotent: every step can run again. Installs Docker, turns on the firewall and unattended
# security updates, hardens SSH and adds a swap file.
set -euo pipefail
ADMIN_USER="$1"
export DEBIAN_FRONTEND=noninteractive

echo "==> Packages"
apt-get update -q
apt-get install -y -q ca-certificates curl ufw unattended-upgrades rsync python3 lsof

echo "==> Docker Engine + compose plugin (Docker's apt repository)"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/ubuntu $codename stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
fi
# Container logs rotate instead of filling the disk.
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "local", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
systemctl enable docker >/dev/null
systemctl restart docker
usermod -aG docker "$ADMIN_USER"

echo "==> Firewall: SSH, HTTP, HTTPS in; everything else out of reach"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for port in 22 80 443; do ufw allow "$port/tcp" >/dev/null; done
ufw --force enable >/dev/null

echo "==> SSH: keys only, no root, only $ADMIN_USER"
cat > /etc/ssh/sshd_config.d/10-txlens.conf <<CONF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers $ADMIN_USER
X11Forwarding no
CONF
sshd -t
systemctl reload ssh

echo "==> Unattended security updates (no automatic reboot during a demo)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
cat > /etc/apt/apt.conf.d/52txlens-unattended <<'CONF'
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF
systemctl enable --now unattended-upgrades >/dev/null

echo "==> 4 GB swap file (the stack is sized for 4 GB RAM)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  [[ -f /swapfile ]] || { fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null; }
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-txlens.conf
sysctl -q --system

echo "==> App folder"
install -d -o "$ADMIN_USER" -g "$ADMIN_USER" -m 0750 /opt/txlens
echo "    host setup done"
