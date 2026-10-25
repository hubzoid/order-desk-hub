"""Load `distributor-v1` into the disposable ERPNext test site, inside its container.

    docker compose -p hubzoid-erpnext-test -f testing/erpnext/compose.yaml exec -T backend \
      bash -c 'cd /home/frappe/frappe-bench/sites && ../env/bin/python - hubzoid-test.localhost' \
      < testing/erpnext/seed.py

It refuses any site whose name is not `hubzoid-test.localhost`. It creates the
synthetic company in UTC with fiscal years 1999 and 2000, the masters and prices
with the dataset's own ids, stock, the purchase orders, receipts and bills, and
the sales orders, deliveries and invoices, so the ERP holds what the sample and
fake REST backends hold. Then three users: `hubzoid-read` (read and print),
`hubzoid-draft` (a custom role that can create the two drafts, never submit) and
`hubzoid-submit` (submit only). Their API keys go to a file inside the
container for an administrator to load with `hubzoid secrets set --stdin`;
they are never printed.
"""
import json
import sys
from pathlib import Path

import frappe

SITE = sys.argv[1] if len(sys.argv) > 1 else "hubzoid-test.localhost"
if SITE != "hubzoid-test.localhost":
    raise SystemExit("This seed only runs on the disposable hubzoid-test.localhost site.")
DATA = Path("/tmp/distributor-v1/erp")       # copied in with: docker compose cp erp/datasets/distributor-v1 backend:/tmp/
COMPANY = "Synthetic Meridian Distribution"

frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")


def load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def ensure(doc, *, name=None, submit=False, child_names=False):
    if frappe.db.exists(doc["doctype"], name or doc.get("name")):
        return frappe.get_doc(doc["doctype"], name or doc.get("name"))
    clean = {k: v for k, v in doc.items() if k not in ("modified", "docstatus", "status", "note") and v is not None}
    d = frappe.get_doc(clean)
    d.insert(ignore_permissions=True, set_name=name or doc.get("name"), set_child_names=not child_names)
    if submit:
        d.submit()
    return d


# -- company, fiscal years, settings ---------------------------------------------------------------
if not frappe.db.exists("Company", COMPANY):
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

    setup_complete({"currency": "USD", "country": "United States", "timezone": "UTC", "language": "English",
                    "company_name": COMPANY, "company_abbr": "SMD", "chart_of_accounts": "Standard",
                    "fy_start_date": "2000-01-01", "fy_end_date": "2000-12-31", "full_name": "Synthetic Admin",
                    "email": "admin@synthetic-meridian.example", "setup_demo": 0})
for year in ("1999", "2000"):
    if not frappe.db.exists("Fiscal Year", year):
        frappe.get_doc({"doctype": "Fiscal Year", "year": year, "year_start_date": f"{year}-01-01",
                        "year_end_date": f"{year}-12-31"}).insert(ignore_permissions=True)
frappe.db.set_single_value("System Settings", "time_zone", "UTC")
frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

# -- masters -----------------------------------------------------------------------------------------
for w in load("warehouses"):
    if not frappe.db.exists("Warehouse", w["name"]):
        frappe.get_doc({"doctype": "Warehouse", "warehouse_name": w["warehouse_name"], "company": COMPANY}).insert(
            ignore_permissions=True)
for pl in load("price_lists"):
    ensure({k: v for k, v in pl.items()}, name=pl["name"])
for doc in load("customers") + load("suppliers"):
    ensure(doc)
for doc in load("items"):
    ensure(dict(doc, uoms=[{"uom": u["uom"], "conversion_factor": u["conversion_factor"]} for u in doc["uoms"]]))
for doc in load("item_prices"):
    ensure(doc)

# -- stock: one reconciliation sets every bin -------------------------------------------------------------
if not frappe.db.exists("Stock Reconciliation", {"remarks": "hubzoid synthetic opening stock"}):
    rows = [{"item_code": b["item_code"], "warehouse": b["warehouse"], "qty": b["actual_qty"], "valuation_rate": 1}
            for b in load("bins") if b["actual_qty"]]
    sr = frappe.get_doc({"doctype": "Stock Reconciliation", "company": COMPANY, "posting_date": "1999-12-01",
                         "set_posting_time": 1, "purpose": "Opening Stock", "remarks": "hubzoid synthetic opening stock",
                         "items": rows})
    sr.insert(ignore_permissions=True)
    sr.submit()

# -- purchasing ------------------------------------------------------------------------------------------
for po in load("purchase_orders"):
    ensure(dict(po, items=[{k: v for k, v in r.items() if k not in ("received_qty", "amount", "stock_qty")}
                           for r in po["items"]]), submit=po["docstatus"] == 1, child_names=True)
for pr in load("purchase_receipts"):
    ensure(dict(pr, items=[{k: v for k, v in r.items() if k not in ("amount", "stock_qty")} for r in pr["items"]]),
           submit=True, child_names=True)
for pi in load("purchase_invoices"):
    d = ensure(dict(pi, items=[{k: v for k, v in r.items() if k not in ("amount", "stock_qty")} for r in pi["items"]]),
               submit=pi["docstatus"] >= 1, child_names=True)
    if pi["docstatus"] == 2 and d.docstatus == 1:
        d.cancel()

# -- selling ---------------------------------------------------------------------------------------------
for so in load("sales_orders"):
    d = ensure(dict(so, items=[{k: v for k, v in r.items() if k not in ("delivered_qty", "amount", "stock_qty")}
                               for r in so["items"]]), submit=so["docstatus"] >= 1, child_names=True)
    if so["docstatus"] == 2 and d.docstatus == 1:
        d.cancel()
    delivered = [r for r in so["items"] if r.get("delivered_qty")]
    if so["docstatus"] == 1 and delivered and not frappe.db.exists("Delivery Note Item", {"against_sales_order": so["name"]}):
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

        dn = make_delivery_note(so["name"])
        dn.items = [i for i in dn.items if any(r["name"] == i.so_detail for r in delivered)]
        for i in dn.items:
            i.qty = next(r["delivered_qty"] for r in delivered if r["name"] == i.so_detail)
        dn.posting_date = so["delivery_date"]
        dn.set_posting_time = 1
        dn.insert(ignore_permissions=True)
        dn.submit()
for si in load("sales_invoices"):
    ensure(dict(si, items=[{k: v for k, v in r.items() if k not in ("amount",)} for r in si["items"]]),
           submit=True, child_names=True)

# -- the three integration users ---------------------------------------------------------------------------
if not frappe.db.exists("Role", "Hubzoid Draft"):
    frappe.get_doc({"doctype": "Role", "role_name": "Hubzoid Draft", "desk_access": 0}).insert(ignore_permissions=True)
from frappe.permissions import add_permission, update_permission_property  # noqa: E402

for dt in ("Sales Order", "Purchase Invoice"):
    add_permission(dt, "Hubzoid Draft", 0)
    for prop in ("read", "write", "create"):
        update_permission_property(dt, "Hubzoid Draft", 0, prop, 1)
    for prop in ("submit", "cancel", "amend", "delete"):
        update_permission_property(dt, "Hubzoid Draft", 0, prop, 0)
keys = {}
for user, roles in (("hubzoid-read@synthetic-meridian.example", ["Sales User", "Purchase User", "Stock User"]),
                    ("hubzoid-draft@synthetic-meridian.example", ["Hubzoid Draft", "Stock User"]),
                    ("hubzoid-submit@synthetic-meridian.example", ["Sales Manager", "Accounts User"])):
    if not frappe.db.exists("User", user):
        frappe.get_doc({"doctype": "User", "email": user, "first_name": user.split("@")[0], "send_welcome_email": 0,
                        "user_type": "System User", "roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)
    from frappe.core.doctype.user.user import generate_keys

    out = generate_keys(user)
    keys[user] = f"{out['api_key']}:{out['api_secret']}"
Path("/tmp/hubzoid-erpnext-keys.json").write_text(json.dumps(keys))
frappe.db.commit()
print("seeded hubzoid-test.localhost; API keys in /tmp/hubzoid-erpnext-keys.json inside the container")
