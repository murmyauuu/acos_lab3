#!/bin/sh
set -eu

USERNAME="${TENANT_LOGIN:-tenant}"
PASSWORD="${TENANT_PASSWORD:-tenant}"

if ! id "$USERNAME" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$USERNAME"
fi

echo "$USERNAME:$PASSWORD" | chpasswd
usermod -aG sudo "$USERNAME"
mkdir -p /var/run/sshd
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config || true

exec /usr/sbin/sshd -D -e
