# Agentic CRM — Phase 1

An agentic CRM system built on top of ERPNext, designed for Kenyan SMEs.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Channels (Phase 2)                      │
│              WhatsApp · SMS · Web Chat · Voice               │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              integration/ (FastAPI service)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  agents/    │  │   tools/     │  │      api/          │  │
│  │  (Phase 2)  │  │  (Phase 2)   │  │   (Phase 2)        │  │
│  └─────────────┘  └──────┬───────┘  └────────────────────┘  │
│                          │                                    │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │              app/erp/  ← THE ONLY ERPNext CALLER       │  │
│  │  client.py · customers.py · catalog.py · sales.py      │  │
│  │  payments.py (stub) · schemas.py                        │  │
│  └───────────────────────┬───────────────────────────────┘  │
└──────────────────────────┼─────────────────────────────────-┘
                           │ HTTP / REST
┌──────────────────────────▼──────────────────────────────────┐
│                        ERPNext v15                           │
│          Customers · Items · Pricing · Inventory             │
│          Sales Orders · Invoices · Payments                  │
└─────────────────────────────────────────────────────────────┘
```

## Non-Negotiable Rules

1. **`integration/app/erp/` is the ONLY code that calls ERPNext.**
2. **All mutating calls take an `idempotency_key`.**
3. **Money/stock calculations NEVER happen in this service — ERPNext does them.**
4. **Idempotency on everything that moves money or stock.**

## Phase 1 Scope

- ERPNext client layer with auth, retries, error mapping
- Customer lookup/creation by phone number
- Product catalog: items, prices, stock
- Sales flow: Quotation → Sales Order → Invoice
- Payment recording stub (Phase 2: real M-Pesa STK push)
- Full test suite with mocked ERPNext API

## Getting Started

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- Python 3.11+ (for local development)

### 1. Clone and configure

```bash
git clone <repo>
cd Erpnow
cp .env.example .env
# Edit .env with your values
```

### 2. Start the stack

```bash
docker compose up -d
```

### 3. First-time ERPNext setup

```bash
# Create the site
docker compose exec backend bench new-site crm.local \
  --mariadb-root-password root \
  --admin-password admin \
  --install-app erpnext

# Run migrations
docker compose exec backend bench --site crm.local migrate

# Add the site to nginx
docker compose exec backend bench --site crm.local set-config developer_mode 1
docker compose restart frontend
```

### 4. Configure ERPNext API credentials

1. Log into ERPNext at http://localhost:8080 (user: `Administrator`, password: `admin`)
2. Go to **Settings → API Access → Generate Keys**
3. Copy the API Key and Secret into your `.env`
4. Set `ERPNEXT_COMPANY` to match the company name in ERPNext
5. Set `ERPNEXT_DEFAULT_WAREHOUSE` to an existing warehouse name

### 5. Custom field setup

The integration expects a `custom_phone` field on the Customer doctype for reliable phone lookup.

In ERPNext:
- **Customize Form → Customer → Add Field**
  - Label: `Custom Phone`
  - Field Name: `custom_phone`
  - Field Type: `Data`
  - In List View: checked

Or via bench:
```bash
docker compose exec backend bench --site crm.local execute \
  "frappe.get_doc({'doctype':'Custom Field','dt':'Customer','fieldname':'custom_phone','label':'Custom Phone','fieldtype':'Data','insert_after':'mobile_no'}).insert()"
```

### 6. Development (local, no Docker for the service)

```bash
cd integration
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run the service
uvicorn app.main:app --reload --port 8001
```

## Project Structure

```
Erpnow/
├── .env.example
├── docker-compose.yml
├── README.md
│
├── integration/          # FastAPI service (Phase 1: ERP client layer)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── app/
│       ├── main.py       # FastAPI entrypoint
│       ├── config.py     # Pydantic settings + feature flags
│       ├── deps.py       # FastAPI dependency injection
│       ├── erp/          # ← THE ONLY ERPNext callers
│       │   ├── client.py     # HTTP client, auth, retries, error mapping
│       │   ├── schemas.py    # Pydantic models for ERPNext docs
│       │   ├── customers.py  # find_or_create_customer
│       │   ├── catalog.py    # get_products, get_price, check_stock
│       │   ├── sales.py      # create_quotation, create_sales_order, submit_sales_invoice
│       │   └── payments.py   # record_payment (Phase 2 stub)
│       ├── channels/     # (Phase 2) channel gateway
│       ├── agents/       # (Phase 2) LLM agents
│       ├── tools/        # (Phase 2) agent tool registry
│       ├── jobs/         # (Phase 2) background jobs
│       ├── db/           # (Phase 2) Postgres models
│       └── api/          # (Phase 2) REST endpoints
│
├── frappe-app/           # (Phase 2) Custom Frappe app
└── dashboard/            # (Phase 2) React dashboard
```

## Environment Variables

See `.env.example` for full documentation. Key variables:

| Variable | Description |
|---|---|
| `ERPNEXT_BASE_URL` | ERPNext instance URL |
| `ERPNEXT_API_KEY` | ERPNext API key |
| `ERPNEXT_API_SECRET` | ERPNext API secret |
| `ERPNEXT_COMPANY` | Company name in ERPNext |
| `ERPNEXT_DEFAULT_WAREHOUSE` | Default warehouse for stock checks |
| `ERPNEXT_PRICE_LIST` | Default price list (e.g. "Standard Selling") |
| `FEATURE_WEBSITE_BUY` | Enable web purchase flow |
| `FEATURE_EXTRA_CHANNELS` | Enable additional channel integrations |

## Running Tests

```bash
cd integration
pytest tests/ -v --cov=app --cov-report=term-missing
```

Tests use `respx` to mock all HTTP calls to ERPNext — no live ERPNext instance needed.

## Phase Roadmap

| Phase | Scope |
|---|---|
| **1 (this)** | ERPNext client layer, schemas, tests, Docker |
| 2 | M-Pesa STK push, WhatsApp channel, LLM agent |
| 3 | Web dashboard, advanced analytics, multi-company |
