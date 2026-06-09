# frappe-app — Custom Frappe Application (Phase 2)

This directory will contain a custom Frappe/ERPNext application that extends ERPNext
with CRM-specific doctypes, custom fields, and server-side scripts.

## Planned Phase 2 additions

### Custom fields
- `Customer.custom_phone` — normalised phone field for reliable lookup by the integration service.
- `Customer.crm_id` — link back to the CRM conversation ID.

### Custom doctypes
- `CRM Conversation` — tracks chat sessions per customer/channel.
- `CRM Message` — individual messages within a conversation.
- `Payment Request Log` — M-Pesa STK push audit trail.

### Hooks / Server scripts
- `on_submit` on Sales Invoice — trigger M-Pesa STK push via integration service webhook.
- `after_insert` on Payment Entry — notify integration service of confirmed payment.

## Quick bootstrap (after Phase 2)

```bash
docker compose exec backend bench new-app agentic_crm
docker compose exec backend bench --site crm.local install-app agentic_crm
docker compose exec backend bench --site crm.local migrate
```

## Custom field installation (Phase 1 — manual)

The `custom_phone` field on Customer is required by Phase 1.
Install it manually via ERPNext UI or bench console:

```bash
docker compose exec backend bench --site crm.local execute \
  "frappe.get_doc({'doctype':'Custom Field','dt':'Customer','fieldname':'custom_phone',\
'label':'Custom Phone','fieldtype':'Data','insert_after':'mobile_no'}).insert(ignore_permissions=True); \
frappe.db.commit()"
```
