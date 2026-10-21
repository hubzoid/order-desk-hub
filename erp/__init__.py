"""The ERP module of the ready-made desks: ERPNext first.

One clean boundary, so the Hubzoid SDK can absorb it later and the Customer
Desk can keep an exact copy of the read parts (see erp/README.md):

* reads: typed and allowlisted (`erp.read.ERPReader`), with company,
  warehouse and `party_scope` enforced by the adapter;
* writes: only `create_draft(kind, payload, external_ref)`, `get(name)` and
  `submit_approved(name, approved_digest)` (`erp.write.DraftWriter`);
* backends: `sample` (the `distributor-v1` dataset, no network), `fake_rest`
  (the real Frappe REST client against a loopback fake site) and
  `frappe_rest` (a live ERPNext site).

The module never imports a model runtime and never decides a write mode:
the hub's `work-items.yaml` (`erp.submit: off | draft | approve`) and the
Hubzoid operation ledger do.
"""

__version__ = "0.1.0"
#: The canonical home of this module. A vendored copy records the commit it came from.
CANONICAL = "hubzoid/order-desk-hub:erp/"
