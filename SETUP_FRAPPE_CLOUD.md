# DEWMIX Hardware — ERPNext on Frappe Cloud
## Phone-friendly setup guide (~10 minutes)

---

## Step 1 — Create a Frappe Cloud account

1. Open **frappecloud.com** in your phone browser
2. Tap **Sign Up** → enter email + password
3. Verify your email

---

## Step 2 — Create an ERPNext site

1. Dashboard → **New Site**
2. Select **Frappe Cloud** shared hosting (free tier works for testing)
3. Choose latest **ERPNext**
4. Site name: `dewmix` → URL will be `dewmix.frappe.cloud`
5. Set an admin password — write it down
6. **Create Site** — wait ~5 minutes

---

## Step 3 — First-time configuration

Open `https://dewmix.frappe.cloud`, login as `Administrator`.

**Setup Wizard** (runs automatically on first login):
- Country: Kenya
- Currency: **KES**
- Company Name: **DEWMIX Hardware**
- Abbreviation: **DX**
- Chart of Accounts: Kenya — IFRS

**Create a Warehouse**
Stock → Warehouses → New
- Name: `Stores` → becomes `Stores - DX`

**Create Price List**
Selling → Price Lists → New
- Name: `Standard Selling`, Currency: KES, Selling ✓

**Add Kenya 16% VAT**
Accounting → Sales Taxes and Charges Template → New
- Title: `Kenya VAT 16%`
- Add row: Type = On Net Total, Rate = 16, Account = `VAT - DX`
- Set as default ✓

**Add a test item** (required for smoke test)
Stock → Items → New
- Item Code: `TEST-PIPE-001`
- Item Name: `1/2 inch PVC Pipe (3m)`
- Is Stock Item ✓

Stock → Item Prices → New
- Item: `TEST-PIPE-001`, Price List: Standard Selling, Rate: `350`

Stock → Stock Reconciliation → New
- Purpose: Opening Stock, Warehouse: `Stores - DX`
- Row: Item = TEST-PIPE-001, Qty = 100 → Submit

---

## Step 4 — Get API credentials

Settings → My Profile → API Access → **Generate Keys**

Copy:
- **API Key** → `ERPNEXT_API_KEY`
- **API Secret** → `ERPNEXT_API_SECRET`

---

## Step 5 — Fill in integration/.env

```
ERPNEXT_BASE_URL=https://dewmix.frappe.cloud
ERPNEXT_API_KEY=<paste here>
ERPNEXT_API_SECRET=<paste here>
ERPNEXT_COMPANY=DEWMIX Hardware
ERPNEXT_DEFAULT_WAREHOUSE=Stores - DX
ERPNEXT_PRICE_LIST=Standard Selling
```

---

## Step 6 — Encrypt credentials

```bash
cd integration
python scripts/encrypt_env.py keygen     # creates .env.key
python scripts/encrypt_env.py encrypt    # creates .env.enc (safe to commit)
```

Add to `.gitignore`:
```
.env
.env.key
```

---

## Step 7 — Run the Phase 1 smoke test

```bash
cd integration
python scripts/smoke_test.py
```

All green = Phase 1 complete.

```
✓ Auth works
✓ get_products: 1 item — 1/2 inch PVC Pipe (3m)
✓ get_price: KES 350.00
✓ check_stock @ Stores - DX: 100 available
✓ find_or_create_customer → CUST-0001
✓ Sales Order: SO-0001 (docstatus=1)
✓ Grand total from ERPNext: KES 350.00
✓ VAT from ERPNext: KES 56.00
✓ Invoice: SINV-0001
✓ Idempotency: no duplicate on second call

Phase 1 acceptance criteria: PASSED ✓
```
