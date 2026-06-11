#!/usr/bin/env bash
#
# Stand up a local ERPNext (via frappe_docker) and configure it for DEWMIX Hardware.
# Idempotent-ish: safe to re-run; skips steps already done.
#
# Prereqs: Docker daemon running, ~4GB free RAM, network access.
#
# Usage:
#   ./scripts/setup_local_erpnext.sh
#
# Produces a working ERPNext at http://localhost:8080 (Administrator / admin)
# with: DEWMIX Hardware company (KES), Stores - DX warehouse, Standard Selling
# price list, Kenya VAT 16% template + tax rule, custom_phone field, and a test
# item (TEST-PIPE-001 @ KES 350, 100 in stock).
#
set -euo pipefail

FRAPPE_DIR="${FRAPPE_DOCKER_DIR:-/home/user/frappe_docker}"
COMPOSE="docker compose -f $FRAPPE_DIR/pwd.yml"
SITE="frontend"

echo "==> 1/6 Ensuring frappe_docker is present"
if [ ! -d "$FRAPPE_DIR" ]; then
  git clone --depth 1 https://github.com/frappe/frappe_docker.git "$FRAPPE_DIR"
fi

echo "==> 2/6 Configuring Docker Hub mirror (bypass anonymous rate limits)"
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://mirror.gcr.io"] }
EOF
  pkill dockerd 2>/dev/null || true
  sleep 3
  (dockerd >/tmp/dockerd.log 2>&1 &)
  sleep 8
fi

echo "==> 3/6 Starting the ERPNext stack"
$COMPOSE up -d

echo "==> 4/6 Waiting for site creation to finish (installs ERPNext)"
for i in $(seq 1 60); do
  st=$(docker inspect -f '{{.State.Status}}' frappe_docker-create-site-1 2>/dev/null || echo missing)
  [ "$st" = "exited" ] && { echo "    site ready"; break; }
  sleep 10
done

echo "==> 5/6 Generating API keys for Administrator"
$COMPOSE exec -T backend bench --site "$SITE" execute \
  frappe.core.doctype.user.user.generate_keys --args "['Administrator']"

echo "==> 6/6 Configuring DEWMIX (company, VAT, warehouse, test item)"
# Setup wizard
$COMPOSE exec -T backend bench --site "$SITE" execute \
  frappe.desk.page.setup_wizard.setup_wizard.setup_complete \
  --kwargs "{'args': {'language':'English','country':'Kenya','timezone':'Africa/Nairobi','currency':'KES','company_name':'DEWMIX Hardware','company_abbr':'DX','chart_of_accounts':'Standard','fy_start_date':'2026-01-01','fy_end_date':'2026-12-31','setup_demo':0}}" \
  2>/dev/null || echo "    (setup wizard may already be complete)"

# DEWMIX entities
$COMPOSE cp "$(dirname "$0")/dewmix_seed.py" backend:/tmp/dewmix_seed.py
$COMPOSE exec -T backend bash -lc \
  "cd /home/frappe/frappe-bench && echo \"exec(open('/tmp/dewmix_seed.py').read())\" | bench --site $SITE console"

echo ""
echo "Done. ERPNext: http://localhost:8080  (Administrator / admin)"
echo "Copy the API key/secret printed in step 5 into integration/.env, then run:"
echo "    cd integration && python scripts/smoke_test.py"
