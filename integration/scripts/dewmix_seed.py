"""
Seed a fresh ERPNext site with DEWMIX Hardware entities.

Run inside the frappe backend container:
    bench --site frontend console < dewmix_seed.py

Idempotent: every entity is created only if it does not already exist.
Creates: custom_phone field, Kenya VAT 16% template + account + tax rule,
TEST-PIPE-001 item with a KES 350 price and 100 units of opening stock.
"""
import frappe

COMPANY = "DEWMIX Hardware"
ABBR = "DX"
WAREHOUSE = f"Stores - {ABBR}"
PRICE_LIST = "Standard Selling"
ITEM_CODE = "TEST-PIPE-001"


def ensure_custom_phone():
    if not frappe.db.exists("Custom Field", "Customer-custom_phone"):
        frappe.get_doc({
            "doctype": "Custom Field", "dt": "Customer",
            "fieldname": "custom_phone", "label": "Custom Phone",
            "fieldtype": "Data", "insert_after": "mobile_no",
            "in_standard_filter": 1, "in_list_view": 1,
        }).insert(ignore_permissions=True)
        print("created custom_phone field")
    else:
        print("custom_phone field exists")


def ensure_vat_account():
    acct = f"VAT - {ABBR}"
    if not frappe.db.exists("Account", acct):
        parent = frappe.db.get_value(
            "Account", {"company": COMPANY, "account_name": "Duties and Taxes"}, "name"
        )
        if parent:
            frappe.get_doc({
                "doctype": "Account", "account_name": "VAT",
                "parent_account": parent, "company": COMPANY,
                "account_type": "Tax", "tax_rate": 16.0,
            }).insert(ignore_permissions=True)
            print(f"created account {acct}")
    return acct


def ensure_vat_template(acct):
    tpl_title = "Kenya VAT 16%"
    if not frappe.db.exists(
        "Sales Taxes and Charges Template", {"title": tpl_title, "company": COMPANY}
    ):
        frappe.get_doc({
            "doctype": "Sales Taxes and Charges Template",
            "title": tpl_title, "company": COMPANY, "is_default": 1,
            "taxes": [{
                "charge_type": "On Net Total", "account_head": acct,
                "description": "VAT @ 16%", "rate": 16.0,
            }],
        }).insert(ignore_permissions=True)
        print(f"created tax template {tpl_title}")
    else:
        print(f"tax template {tpl_title} exists")


def ensure_tax_rule():
    tpl = frappe.db.get_value(
        "Sales Taxes and Charges Template", {"company": COMPANY, "is_default": 1}, "name"
    )
    if tpl and not frappe.db.exists("Tax Rule", {"sales_tax_template": tpl}):
        frappe.get_doc({
            "doctype": "Tax Rule", "tax_type": "Sales", "company": COMPANY,
            "sales_tax_template": tpl, "priority": 1, "use_for_shopping_cart": 1,
        }).insert(ignore_permissions=True)
        print(f"created tax rule → {tpl}")


def ensure_item():
    if not frappe.db.exists("Item", ITEM_CODE):
        frappe.get_doc({
            "doctype": "Item", "item_code": ITEM_CODE,
            "item_name": "1/2 inch PVC Pipe (3m)", "item_group": "Products",
            "stock_uom": "Nos", "is_stock_item": 1, "is_sales_item": 1,
        }).insert(ignore_permissions=True)
        print(f"created item {ITEM_CODE}")
    if not frappe.db.exists("Item Price", {"item_code": ITEM_CODE, "price_list": PRICE_LIST}):
        frappe.get_doc({
            "doctype": "Item Price", "item_code": ITEM_CODE,
            "price_list": PRICE_LIST, "selling": 1,
            "price_list_rate": 350.0, "currency": "KES",
        }).insert(ignore_permissions=True)
        print("created item price KES 350")


def ensure_stock():
    qty = frappe.db.get_value("Bin", {"item_code": ITEM_CODE, "warehouse": WAREHOUSE}, "actual_qty")
    if qty and qty > 0:
        print(f"stock present: {qty}")
        return
    se = frappe.get_doc({
        "doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
        "company": COMPANY,
        "items": [{
            "item_code": ITEM_CODE, "t_warehouse": WAREHOUSE,
            "qty": 100, "basic_rate": 200, "allow_zero_valuation_rate": 1,
        }],
    })
    se.insert(ignore_permissions=True)
    se.submit()
    print(f"opening stock created: {se.name}")


ensure_custom_phone()
acct = ensure_vat_account()
ensure_vat_template(acct)
ensure_tax_rule()
ensure_item()
ensure_stock()
frappe.db.commit()
print("DEWMIX seed complete")
