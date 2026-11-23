#!/usr/bin/env bash
# Dev server: build this clone and run it (deploy/docker-compose.dev-server.yml).
#
#   ./deploy/dev-server.sh                 git pull, build, start everything, show status
#   ./deploy/dev-server.sh up --profile dialer   the same, plus the outbound sales dialer
#   ./deploy/dev-server.sh logs [service]  follow logs (all services, or one: api, worker, jobs…)
#   ./deploy/dev-server.sh ps              status
#   ./deploy/dev-server.sh restart [svc]   restart without rebuilding
#   ./deploy/dev-server.sh down            stop everything (data volumes are kept)
#   ./deploy/dev-server.sh exec api python -m scripts.seed_web_demo     any other compose command
#
# Host already has a reverse proxy on 80/443 (EDGE_* in dev-server.env):
#   ./deploy/dev-server.sh edge-setup      add our site file to that nginx, get the certificate, reload
#   ./deploy/dev-server.sh edge-remove     take our site file out again
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=deploy/dev-server.env
BACKEND_ENV=AI-receptionist-1/.env

[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE: cp deploy/dev-server.env.example $ENV_FILE and fill it in." >&2; exit 1; }
[ -f "$BACKEND_ENV" ] || { echo "Missing $BACKEND_ENV: cp AI-receptionist-1/.env.example $BACKEND_ENV and fill it in." >&2; exit 1; }

setting() { grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d "\"' " || true; }

[ -n "$(setting REDIS_PASSWORD)" ] || { echo "Set REDIS_PASSWORD in $ENV_FILE (openssl rand -hex 24)." >&2; exit 1; }

API_DOMAIN="$(setting API_DOMAIN)"; APP_DOMAIN="$(setting APP_DOMAIN)"
ADMIN_DOMAIN="$(setting ADMIN_DOMAIN)"; SITE_DOMAIN="$(setting SITE_DOMAIN)"
EDGE_NETWORK="$(setting EDGE_NETWORK)"
EDGE_PROXY="$(setting EDGE_PROXY)"                     # the proxy container, e.g. storexfly_web
EDGE_SITES_DIR="$(setting EDGE_SITES_DIR)"             # host folder that proxy includes *.conf from
EDGE_CERTBOT_CONF="$(setting EDGE_CERTBOT_CONF)"       # its /etc/letsencrypt volume
EDGE_CERTBOT_WWW="$(setting EDGE_CERTBOT_WWW)"         # its /var/www/certbot (webroot) volume
SITE_FILE="${EDGE_SITES_DIR}/laudline.conf"

files=(-f deploy/docker-compose.dev-server.yml)
[ -n "$EDGE_NETWORK" ] && files+=(-f deploy/docker-compose.dev-server.edge.yml)
compose() { docker compose --env-file "$ENV_FILE" "${files[@]}" "$@"; }

# ── Site file for the host's nginx ──────────────────────────────────────────
# Upstreams are variables resolved through Docker's DNS per request, so the host's nginx still
# starts and reloads when this stack is down (it answers 502 for our hosts only).

proxy_block() {   # proxy_block <server_name> <upstream>
  cat <<EOF
server {
    listen 443 ssl;
    server_name $1;
    ssl_certificate /etc/letsencrypt/live/${SITE_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${SITE_DOMAIN}/privkey.pem;
    resolver 127.0.0.11 valid=10s ipv6=off;
    client_max_body_size 25m;
    location / {
        set \$laudline_upstream http://$2;
        proxy_pass \$laudline_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$http_connection;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
EOF
}

site_conf() {     # site_conf http|https
  cat <<EOF
# LaudLine dev server (receptionist repo, deploy/dev-server.sh edge-setup). Safe to delete.
server {
    listen 80;
    server_name ${SITE_DOMAIN} ${API_DOMAIN} ${APP_DOMAIN} ${ADMIN_DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
  if [ "$1" = https ]; then
    proxy_block "$API_DOMAIN" laudline-api:8002
    proxy_block "$APP_DOMAIN" laudline-app:8080
    proxy_block "$ADMIN_DOMAIN" laudline-admin:8080
    proxy_block "$SITE_DOMAIN" laudline-site:8080
  fi
}

write_site() {    # write_site <content>; sudo only when the folder isn't ours
  if [ -w "$EDGE_SITES_DIR" ]; then printf '%s\n' "$1" > "$SITE_FILE"; else printf '%s\n' "$1" | sudo tee "$SITE_FILE" >/dev/null; fi
}

remove_site() { if [ -w "$EDGE_SITES_DIR" ]; then rm -f "$SITE_FILE"; else sudo rm -f "$SITE_FILE"; fi; }

reload_edge() {   # test first; on failure take our file out so the other sites keep working
  if docker exec "$EDGE_PROXY" nginx -t; then
    docker exec "$EDGE_PROXY" nginx -s reload
  else
    echo "nginx -t failed: removing $SITE_FILE so $EDGE_PROXY keeps serving its own sites." >&2
    remove_site
    docker exec "$EDGE_PROXY" nginx -t && docker exec "$EDGE_PROXY" nginx -s reload
    exit 1
  fi
}

need_edge() {
  for v in EDGE_NETWORK EDGE_PROXY EDGE_SITES_DIR EDGE_CERTBOT_CONF EDGE_CERTBOT_WWW SITE_DOMAIN API_DOMAIN APP_DOMAIN ADMIN_DOMAIN; do
    [ -n "${!v}" ] || { echo "Set $v in $ENV_FILE first." >&2; exit 1; }
  done
}

cmd="${1:-up}"
[ $# -gt 0 ] && shift
case "$cmd" in
  up)
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "==> git pull"
      git pull --ff-only
    fi
    echo "==> build and start"
    profiles=()
    if [ "${1:-}" = "--profile" ] && [ -n "${2:-}" ]; then profiles=(--profile "$2"); fi
    compose ${profiles[@]+"${profiles[@]}"} up -d --build --remove-orphans
    docker image prune -f >/dev/null || true
    compose ps
    ;;
  edge-setup)
    need_edge
    echo "==> 1/3 HTTP site for the certificate challenge"
    write_site "$(site_conf http)"
    reload_edge
    if docker run --rm -v "$EDGE_CERTBOT_CONF":/etc/letsencrypt -v "$EDGE_CERTBOT_WWW":/var/www/certbot \
         certbot/certbot certificates --cert-name "$SITE_DOMAIN" 2>/dev/null | grep -q "Certificate Name: $SITE_DOMAIN"; then
      echo "==> 2/3 certificate for $SITE_DOMAIN already exists"
    else
      echo "==> 2/3 certificate for $SITE_DOMAIN, $API_DOMAIN, $APP_DOMAIN, $ADMIN_DOMAIN"
      docker run --rm -v "$EDGE_CERTBOT_CONF":/etc/letsencrypt -v "$EDGE_CERTBOT_WWW":/var/www/certbot \
        certbot/certbot certonly --webroot -w /var/www/certbot --cert-name "$SITE_DOMAIN" \
        -d "$SITE_DOMAIN" -d "$API_DOMAIN" -d "$APP_DOMAIN" -d "$ADMIN_DOMAIN" \
        --non-interactive --agree-tos --register-unsafely-without-email --keep-until-expiring
    fi
    echo "==> 3/3 HTTPS sites"
    write_site "$(site_conf https)"
    reload_edge
    echo "Done: https://$SITE_DOMAIN  https://$APP_DOMAIN  https://$ADMIN_DOMAIN  https://$API_DOMAIN/docs"
    ;;
  edge-remove)
    need_edge
    remove_site
    docker exec "$EDGE_PROXY" nginx -t && docker exec "$EDGE_PROXY" nginx -s reload
    ;;
  logs) compose logs -f --tail=200 "$@" ;;
  *)    compose "$cmd" "$@" ;;
esac
