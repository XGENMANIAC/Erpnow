# DEWMIX Hardware — Agentic CRM

AI-powered WhatsApp sales and order management for **DEWMIX Hardware**, Nyeri Highway, Kenya.

**Business:** 3,000+ hardware products across Tools, Pipes, Bathroom Fittings, Locks, Nails,
Paints, Electricals, and Roofing. WhatsApp: **+254 787 151 516**.

**What this system does:** AI agents handle WhatsApp conversations, take orders, generate
invoices, and trigger M-Pesa payment — all backed by ERPNext as the authoritative source of
stock, pricing, and accounting. The existing website (`dewmix-hardware.vercel.app`) is
integrated via the website quote webhook (Phase 6).

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

### 3. ERPNext — DEWMIX configuration

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

### 4. Configure ERPNext for DEWMIX Hardware

After first-time setup, perform these DEWMIX-specific steps in the ERPNext desk UI:

**Company & Currency**
- Company name: `DEWMIX Hardware`
- Default currency: `KES`
- Create a `Standard Selling` price list in KES

**Warehouses** (Inventory → Warehouses)
- Create `Stores - DX` as the main stock warehouse

**Tax template** (Accounting → Tax → Sales Tax Template)
- Create `Kenya VAT 16%`: Line item `Output Tax @ 16%`, account `VAT - DX`
- Assign this template as default on Sales Order and Sales Invoice

**Item Groups** (map to the 8 DEWMIX categories)
- TOOLS, PIPES, BATHROOM N TOILETS, DOOR LOCKS, NAILS N SCREWS,
  PAINTS, ELECTRICALS, ROOFING

**API credentials**
1. Log into ERPNext at http://localhost:8080 (user: `Administrator`, password: `admin`)
2. Go to **Settings → API Access → Generate Keys**
3. Copy the API Key and Secret into your `.env`
4. Set `ERPNEXT_COMPANY=DEWMIX Hardware`
5. Set `ERPNEXT_DEFAULT_WAREHOUSE=Stores - DX`

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

## Agent Prompts

System prompts live in `integration/app/agents/prompts/` and are editable Markdown files.
Each contains `[REVIEW]` markers where DEWMIX-specific business policy is needed:

| File | Agent | Key `[REVIEW]` items |
|---|---|---|
| `conversation.md` | Customer chat (WhatsApp) | Tone, high-value threshold, escalation policy |
| `orchestrator.md` | Intent → ERPNext tool calls | M-Pesa shortcode, high-value threshold |
| `catalog.md` | Product Q&A | Exclusive brands, compatibility rules |
| `collections.md` | Unpaid invoice follow-up | Credit terms, collection timeline |
| `summary.md` | Daily ops recap | Recipient, cost alert threshold |
| `support.md` | Post-sale support | Delivery radius, return policy |

**Before Phase 4:** resolve all `[REVIEW]` items with the DEWMIX business owner.

## Phase Roadmap

| Phase | Scope |
|---|---|
| **1 (done)** | ERPNext client layer, schemas, 45 passing tests, Docker |
| **2 (next)** | M-Pesa Daraja STK push + idempotent payment recording |
| **3** | WhatsApp Cloud API channel + webhook gateway |
| **4** | LLM agents (NIM) + orchestrator + guardrails + cost tracking |
| **5** | Thin Next.js dashboard: live chats, human takeover, daily summary |
| **6** | Collections agent, website buy button, extra channels (all behind flags) |

## Website Integration

The existing website `dewmix-hardware.vercel.app` already sends customers to WhatsApp for
quotes. Phase 6 adds a **"Buy Now" webhook** from the website that enters the Phase 4 order
flow directly — same ERPNext tools, no agent chat needed. Enabled via `FEATURE_WEBSITE_BUY=true`.

The website's product images are stored in Supabase (project `rwlsugzbgnjbgxlzwnmz`).
The `Supabase Storage` URLs can be attached to ERPNext Item records as the image field.
