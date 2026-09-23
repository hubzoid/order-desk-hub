# `erp/`: the desks' ERPNext module

This folder is the canonical ERP module of the ready-made desks (decision B042).
The Order & Invoice Desk owns it. The Customer Desk keeps an exact copy of the
read parts, in the same layout, marked with the commit it was copied from. The
Hubzoid SDK may absorb it later. It depends on `httpx`, `pydantic` and `PyYAML`
only, and never imports a model runtime.

## The contract

**Reads** are typed and allowlisted (`read.py`, `resources.py`):

| Resource | DocType | Notes |
|---|---|---|
| `customers`, `suppliers` | Customer, Supplier | Masters have no company field. Scope comes from the ERP user's permissions and `party_scope`. |
| `items` | Item | With UOM conversion rows. |
| `prices` | Item Price | Customer and supplier prices hidden from other parties. |
| `availability` | Bin | Only the configured warehouses. |
| `sales_orders` | Sales Order | Delivery and billing status, `per_delivered`, `per_billed`, item rows with `delivered_qty` and `delivery_date`. |
| `purchase_orders`, `purchase_receipts` | Purchase Order, Purchase Receipt | Receipt rows keep `purchase_order` and `purchase_order_item`, accepted `qty`, `stock_qty`, `rejected_qty` and `returned_qty` (stock units). Receipts carry `is_return`. |
| `sales_invoices` | Sales Invoice | With `po_no`, `is_return`, `outstanding_amount`. |
| `purchase_invoices` | Purchase Invoice | Rows keep `po_detail` and `pr_detail`. |

Every record is `{resource, id, company, modified_at, erp_modified, observed_at,
backend, synthetic, fields, rows, source}`. `rows` is a plain list of the record's
child rows (the `items` table of a transaction, the `uoms` table of an item), each
`{row_id, idx, ...allowlisted row fields}`. It is `null` in list results: read the
record for its rows. Money and quantities are Decimal
strings. `ERPContext.party_scope` (set by trusted desk code from a verified
case binding, never by a model) hides every record of another party as
`not_found`. `get_print_pdf` returns the official print of a submitted Sales
Invoice only, with the format from `print_format` in the configuration, a
`%PDF` check and a 5 MiB cap.

**Writes** are three calls and nothing else (`write.py`):

* `create_draft(kind, payload, external_ref)`: a Sales Order or Purchase
  Invoice draft (docstatus 0). Status fields are stripped, `name`, `owner` and
  unknown fields are refused, company, currency, price list and warehouses
  must match the configuration. The external reference becomes a unique
  marker in `custom_hubzoid_operation_id`, so one operation never creates two
  documents, and the business reference (PO number, bill number) is searched
  first, on every page of that party's documents. On a live site nothing is
  inserted until the marker field is proven unique (`require_unique_marker`).
  The draft is read back and compared, the unit conversion included (a
  payload line may carry `expected_conversion_factor`, kept by the desk and
  never sent): any difference is `needs_review`.
* `recover(kind, payload, external_ref)`: after a lost or interrupted create,
  what ERPNext holds for the marker, checked like a fresh create: `absent`,
  `applied` (one inert draft equal to the proposal), `different`,
  `not_inert` or `several`. Only `absent` with a proven unique marker makes a
  retry safe.
* `get(kind, name)`: the reread draft with its `content_digest` and
  `approval_digest` (content plus name, `modified` version and docstatus).
* `submit_approved(kind, name, approved_digest)`: rereads, refuses unless the
  approval digest still matches, submits that exact document (Frappe's own
  `modified` check catches a change in between), with the separate submit
  credential, and reads it back as docstatus 1.

Write modes are not decided here. The hub's `work-items.yaml` sets
`erp.submit` to `off`, `draft` or `approve`, and the Hubzoid operation ledger
(`hub.operation.run`) records every write, waits for a person in `approve`
mode and turns a lost answer into an `ambiguous` operation that is reconciled
with `find(kind, external_ref)`, never retried blindly.

**Backends**: `sample` (no network, the `distributor-v1` dataset plus a local
state file; like Frappe, an insert drops unknown fields without an error and
an insert with docstatus 1 submits), `fake_rest` (the real REST client against `fake_server.py` on
loopback, with fault injection) and `frappe_rest` (a live site). One test suite
runs the same contract on all three (`tests/test_erp_contract.py`, and
`tests/live/` for the live site).

## Files

| File | Part | Copy to the Customer Desk |
|---|---|---|
| `errors.py`, `config.py`, `resources.py`, `models.py`, `filters.py` | shared | yes |
| `http.py` | the one HTTP path (pinned addresses, no redirects, caps) | yes |
| `read.py` | typed reads | yes |
| `backends/__init__.py`, `backends/sample.py`, `backends/frappe_rest.py` | transports | yes (the Customer Desk never enables writes: `writes_enabled: false`, and its doctor refuses `true`) |
| `fake_server.py` | the fake ERPNext site | yes |
| `service.py`, `doctor.py`, `cli.py` | wiring, checks, `hubzoid-erp` | yes |
| `datasets/` | `distributor-v1` and its generator | yes |
| `digest.py`, `write.py`, `bootstrap.py` | the write contract | no |

A vendored copy adds `erp/VENDORED.md` with the source commit and is never
edited in place. Changes come here first.

## Live ERPNext

The integration user needs read permission on the DocTypes above (plus Company,
Warehouse and Price List), print permission on Sales Invoice, create on Sales
Order and Purchase Invoice for draft writes, and nothing else. Submission uses a
second user (`submit_credential_ref`) that may only submit those two DocTypes.
An administrator installs the one custom field with `hubzoid-erp bootstrap`.
Doctor blocks live writes until that field is unique on both DocTypes and
nothing on the site acts on a draft save (server scripts, webhooks,
notifications, workflows, assignment rules, auto repeat, or an unqualified app).
