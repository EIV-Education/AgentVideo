#!/usr/bin/env bash
# Install or update AgentVideo on a VPS that already runs other sites.
#
#   sudo bash deploy/install.sh                      # app only, on 127.0.0.1:HOST_PORT
#   sudo bash deploy/install.sh video.example.com    # app + nginx site + HTTPS for that subdomain
#
# Safe by design: never touches existing nginx sites, validates with `nginx -t` before a
# zero-downtime reload, and rolls back its own config file if validation fails.
set -euo pipefail

DOMAIN="${1:-}"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DEPLOY_DIR"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
command -v docker >/dev/null || die "Docker is not installed. See deploy/DEPLOY.md, step 1."
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing (apt install docker-compose-plugin)"

# 1. Settings
if [ ! -f .env ]; then
  cp .env.example .env
  read -rp "Anthropic API key (sk-ant-...): " key
  read -rp "Shared password for the team: " pass
  [ -n "$key" ] && [ -n "$pass" ] || die "API key and password are required"
  sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${key}|; s|^APP_PASSWORD=.*|APP_PASSWORD=${pass}|" .env
  chmod 600 .env
  say "Saved settings to deploy/.env (readable by root only)"
fi
grep -q '^ANTHROPIC_API_KEY=sk-ant-\.\.\.$' .env && die "edit deploy/.env and set ANTHROPIC_API_KEY"
HOST_PORT="$(grep -E '^HOST_PORT=' .env | cut -d= -f2)"; HOST_PORT="${HOST_PORT:-8510}"

if ! docker ps --format '{{.Names}}' | grep -qx agentvideo; then
  if (ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -qE "[:.]${HOST_PORT}\b"; then
    die "port ${HOST_PORT} is already used by something else; change HOST_PORT in deploy/.env"
  fi
fi

# 2. App container
mkdir -p data && chown -R 10001:10001 data
say "Building and starting the container (first build takes a few minutes)…"
nice -n 10 docker compose build --pull
docker compose up -d
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null || die "app did not start; check: docker compose -f $DEPLOY_DIR/docker-compose.yml logs"
say "App is running on 127.0.0.1:${HOST_PORT} (not exposed to the internet by itself)"

[ -n "$DOMAIN" ] || { say "No domain given: skipped web server setup. Re-run with your subdomain to publish it."; exit 0; }

# 3. Nginx site for the subdomain (only if this VPS uses host nginx)
if [ -d /www/server/panel ]; then
  say "aaPanel detected: not touching its nginx. Finish in the aaPanel UI (deploy/DEPLOY.md, 'aaPanel' section):"
  say "  Website -> Add site ${DOMAIN} -> Reverse proxy to http://127.0.0.1:${HOST_PORT} -> SSL (Let's Encrypt)"
  exit 0
fi
command -v nginx >/dev/null && systemctl is-active --quiet nginx \
  || die "host nginx not found/running. App is up; send the output of check-vps.sh to configure your web server."

if [ -d /etc/nginx/sites-available ]; then
  CONF="/etc/nginx/sites-available/agentvideo.conf"; LINK="/etc/nginx/sites-enabled/agentvideo.conf"
else
  CONF="/etc/nginx/conf.d/agentvideo.conf"; LINK=""
fi
grep -rqs "server_name[^;]*\b${DOMAIN}\b" /etc/nginx --exclude=agentvideo.conf \
  && die "${DOMAIN} is already configured in another nginx file; pick a new subdomain"

sed -e "s/__DOMAIN__/${DOMAIN}/g" -e "s/__PORT__/${HOST_PORT}/g" nginx-agentvideo.conf > "$CONF"
[ -n "$LINK" ] && ln -sf "$CONF" "$LINK"
if ! nginx -t; then
  rm -f "$CONF" ${LINK:+"$LINK"}
  die "nginx config test failed; removed agentvideo.conf, your existing sites were not changed"
fi
systemctl reload nginx
say "nginx reloaded (no downtime). http://${DOMAIN} now points to AgentVideo"

# 4. HTTPS
if command -v certbot >/dev/null; then
  say "Requesting a free HTTPS certificate for ${DOMAIN}…"
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect \
    || say "certbot failed (is the DNS record for ${DOMAIN} pointing to this VPS?). Site still works over http; re-run later."
else
  say "certbot not installed: install it (apt install certbot python3-certbot-nginx) and run: certbot --nginx -d ${DOMAIN}"
fi
say "Done. Open https://${DOMAIN} and log in with any username + the shared password."
