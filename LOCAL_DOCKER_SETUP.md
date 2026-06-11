# DEWMIX — Local ERPNext via Docker

Run a full ERPNext locally and verify Phase 1 end-to-end. This is what was used
to prove Phase 1 against a live instance (not just mocks).

## Prerequisites
- Docker daemon running
- ~4 GB free RAM, a few GB disk
- Network access (first run pulls ~1.5 GB of images)

> Docker Hub rate-limits anonymous pulls. The setup script configures the
> `mirror.gcr.io` registry mirror to avoid this. If you have a Docker Hub
> account, `docker login` also works.

## One-command setup

```bash
cd integration
./scripts/setup_local_erpnext.sh
```

This:
1. Clones `frappe_docker` (if missing)
2. Configures the GCR registry mirror
3. Starts the ERPNext stack (`pwd.yml`) — 11 containers
4. Waits for site creation (ERPNext install)
5. Generates Administrator API keys (printed to console)
6. Seeds DEWMIX: company (KES), `Stores - DX` warehouse, `Standard Selling`
   price list, `Kenya VAT 16%` template + tax rule, `custom_phone` field,
   and `TEST-PIPE-001` (KES 350, 100 in stock)

ERPNext is then at **http://localhost:8080** — `Administrator` / `admin`.

## Wire the integration and test

1. Copy the API key/secret from step 5 into `integration/.env`:

```
ERPNEXT_BASE_URL=http://localhost:8080
ERPNEXT_API_KEY=<from step 5>
ERPNEXT_API_SECRET=<from step 5>
ERPNEXT_COMPANY=DEWMIX Hardware
ERPNEXT_DEFAULT_WAREHOUSE=Stores - DX
ERPNEXT_PRICE_LIST=Standard Selling
ERPNEXT_SALES_TAXES_TEMPLATE=Kenya VAT 16% - DX
```

2. Run the smoke test:

```bash
python scripts/smoke_test.py
```

Expected — 12/12 with VAT applied by ERPNext:

```
✓ get_price(TEST-PIPE-001): KES 350.0
✓ check_stock @ Stores - DX: 100.0 available
✓ Sales Order: SAL-ORD-2026-0000N (docstatus=1)
✓ Grand total from ERPNext: KES 406.0
✓ VAT from ERPNext: KES 56.0
✓ Invoice: ACC-SINV-2026-0000N
✓ Same customer returned — no duplicate created
Phase 1 acceptance criteria: PASSED ✓
```

## Encrypt your credentials

```bash
python scripts/encrypt_env.py keygen     # → .env.key (gitignored)
python scripts/encrypt_env.py encrypt    # → .env.enc (safe to commit)
python scripts/encrypt_env.py decrypt    # prints the restored .env
```

Only secrets (API key/secret, DB/Redis URLs) are encrypted inside `.env.enc`;
everything else stays readable. `.env` and `.env.key` are gitignored.

## Managing the stack

```bash
docker compose -f /home/user/frappe_docker/pwd.yml ps        # status
docker compose -f /home/user/frappe_docker/pwd.yml logs -f   # logs
docker compose -f /home/user/frappe_docker/pwd.yml down      # stop
docker compose -f /home/user/frappe_docker/pwd.yml down -v   # stop + wipe data
```

## Note on this environment

This was set up inside the Claude Code **cloud container**, not a physical
desktop. The container is ephemeral — if it's reclaimed, re-run
`setup_local_erpnext.sh` to rebuild. Anything you want to keep must be
committed and pushed.
