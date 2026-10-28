#!/usr/bin/env bash
# Read-only check of a VPS before installing AgentVideo. Changes nothing.
# Usage: bash deploy/check-vps.sh
set -u
PORT="${HOST_PORT:-8510}"

hr() { printf '\n== %s ==\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

hr "System"
. /etc/os-release 2>/dev/null && echo "OS: ${PRETTY_NAME:-unknown}"
echo "Arch: $(uname -m)"
echo "CPU cores: $(nproc 2>/dev/null || echo ?)"
free -h 2>/dev/null | awk 'NR<=2'
echo "Load (1/5/15 min): $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"

hr "Disk"
df -h / 2>/dev/null | tail -1 | awk '{print "Root: " $4 " free of " $2 " (" $5 " used)"}'

hr "Docker"
if have docker; then
  docker --version
  if docker compose version >/dev/null 2>&1; then docker compose version; else echo "docker compose plugin: MISSING"; fi
  docker ps --format '  running: {{.Names}} ({{.Image}}) {{.Ports}}' 2>/dev/null || echo "  (cannot list containers: run with sudo)"
else
  echo "Docker: NOT INSTALLED"
fi

hr "Web server"
if [ -d /www/server/panel ]; then
  echo "Control panel: aaPanel/BT detected (/www/server/panel)"
  [ -x /www/server/nginx/sbin/nginx ] && /www/server/nginx/sbin/nginx -v 2>&1 | sed 's/^/aaPanel /'
  [ -d /www/server/panel/vhost/nginx ] && echo "aaPanel nginx sites: $(ls /www/server/panel/vhost/nginx | tr '\n' ' ')"
  grep -h client_max_body_size /www/server/nginx/conf/nginx.conf 2>/dev/null | sed 's/^ */aaPanel nginx.conf: /'
fi
for svc in nginx apache2 httpd caddy traefik; do
  if have systemctl && systemctl is-active --quiet "$svc" 2>/dev/null; then echo "$svc: running (systemd)"; fi
done
have nginx && nginx -v 2>&1
have caddy && caddy version 2>/dev/null
have docker && docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -Ei 'nginx|traefik|caddy|apache|httpd' | sed 's/^/container: /'
have certbot && echo "certbot: $(certbot --version 2>&1)" || echo "certbot: not installed"
[ -d /etc/nginx/sites-enabled ] && echo "nginx sites-enabled: $(ls /etc/nginx/sites-enabled | tr '\n' ' ')"
[ -d /etc/nginx/conf.d ] && echo "nginx conf.d: $(ls /etc/nginx/conf.d | tr '\n' ' ')"

hr "Listening ports"
if have ss; then ss -tlnp 2>/dev/null | awk 'NR>1{print "  " $4 "  " $6}' | sort -u; else netstat -tlnp 2>/dev/null; fi
if (ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -qE "[:.]${PORT}\b"; then
  echo "Port ${PORT}: IN USE -> set HOST_PORT to another port in deploy/.env"
else
  echo "Port ${PORT}: free"
fi

hr "Done"
echo "Nothing was changed. Copy everything above and send it back."
