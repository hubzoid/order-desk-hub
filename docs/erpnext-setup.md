# Connecting ERPNext

Start on the sample. Connect a real site only after the desk does what you want on your own documents. The desk never needs your ERP administrator's credential at run time.

## What the site needs

1. **Three users with API keys**, each a System User, never an administrator:

| User | Rights | Used for |
|---|---|---|
| read | Read on Customer, Supplier, Item, Item Price, Bin, Sales Order, Purchase Order, Purchase Receipt, Sales Invoice, Purchase Invoice, Company, Warehouse, Price List. Print on Sales Invoice. Read on Custom Field, Server Script, Webhook, Notification, Workflow, Assignment Rule and Auto Repeat (doctor checks them, and the desk proves the marker field is unique before its first write) | All reads (`credential_ref` when writes are off) |
| draft | A custom role with read, write and create on Sales Order and Purchase Invoice, and **no submit, cancel, amend or delete**. Read on every linked master | Draft writes (`credential_ref` when writes are on) |
| submit | Submit (and write) on Sales Order and Purchase Invoice | Approved submission only (`submit_credential_ref`) |

Do not use Sales User or Accounts User for the draft user: those roles can submit, and Frappe submits a new document posted with docstatus 1.

2. **One custom field**, installed once by an administrator: `custom_hubzoid_operation_id` on Sales Order and Purchase Invoice, Data, unique, read only, not copied, length 140. It is how a lost answer is reconciled without a second document.

```bash
hubzoid-erp bootstrap export . --output output/setup/erpnext-bootstrap.json
# review it, then with an administrator key in the environment (never in a file or chat):
hubzoid-erp bootstrap apply . --manifest output/setup/erpnext-bootstrap.json --admin-credential-ref env:ERP_ADMIN_TOKEN
hubzoid-erp bootstrap verify .
```

3. **No draft-save side effects.** Doctor lists Server Scripts, Webhooks, Notifications, Workflows, Assignment Rules and Auto Repeat on the two document types, and any app beyond frappe and erpnext. Each one must be checked so that saving a draft cannot send, notify, submit or post. Until doctor shows none (or you list a checked app in `qualified_apps`), live writes stay unready.

## Connect, read only first

Credentials go into Hubzoid's secret store by a credential administrator, never into a file or a chat:

```bash
hubzoid secrets set . --ref secret:hub/<hub>/erpnext-read --stdin
```

`connectors/erpnext.yaml`:

```yaml
version: 1
backend: frappe_rest
site_url: https://erp.example.com
credential_ref: secret:hub/<hub>/erpnext-read
submit_credential_ref: null
company: Your Company
currency: USD
warehouses: [Stores - YC]
selling_price_list: Standard Selling
buying_price_list: Standard Buying
writes_enabled: false
```

```bash
hubzoid-erp configure . --file connectors/erpnext.yaml --expected-revision N
hubzoid-erp doctor .
```

Switching from the sample to a live site invalidates earlier proposals: rerun open cases.

## Turn on drafts, then submission

1. When doctor shows `ready_write` prerequisites, set `credential_ref` to the draft user and `writes_enabled: true`, then apply.
2. In `work-items.yaml`, set `erp.submit: draft`. The desk now saves inert drafts that a person finishes in ERPNext.
3. For approved submission, add `submit_credential_ref` (the submit user) and set `erp.submit: approve`. Every submission then waits for the person who asked to approve the exact draft.

## Versions

Doctor shows your site's Frappe and ERPNext versions. Before enabling writes, run the live contract suite in `testing/erpnext/` against a test copy of your site.

Not exposed, by design: cancel, amend, delete, payments, stock reservations, master data changes and any generic method call.
