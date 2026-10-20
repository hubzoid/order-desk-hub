---
tags: [desk, scripted-tools]
expect_tools: [desk_create_draft]
not_contains: ["has been posted", "was posted", "is now posted", "has been booked", "was booked", "order is confirmed"]
fake_tools:
  - tool: desk_case
    output: {"case": "CASE-000003", "case_id": "c-3", "state": "draft_ready", "revision": 5, "kind": "sales_order", "party": {"type": "Customer", "id": "SYN-CUST-001"}, "reference": "HF-7010", "digest": "sha256:0f0bbf2c69b4eb5de0185dcd0953935aa3f1633a0270b75c74b09e059021a17c", "owner": "admin@localhost", "total": "276.6", "actions": ["create_draft", "release", "cancel"], "lines": [{"n": 1, "source": "24 x SYN-ITEM-001 Synthetic Olive Oil 1L", "loc": "message line 11", "item": "SYN-ITEM-001", "qty": "24", "uom": "Nos", "rate": "9.8", "amount": "235.2", "rules": ["The document names ERP item SYN-ITEM-001 exactly", "No unit is written, so the item's stock unit Nos", "SYN-RETAIL price list SYN-IP-0001: 9.8 per Nos"]}, {"n": 2, "source": "12 x SYN-ITEM-003 Synthetic Dish Soap 500ml", "loc": "message line 12", "item": "SYN-ITEM-003", "qty": "12", "uom": "Nos", "rate": "3.45", "amount": "41.4", "rules": ["The document names ERP item SYN-ITEM-003 exactly", "No unit is written, so the item's stock unit Nos", "SYN-RETAIL price list SYN-IP-0003: 3.45 per Nos"]}], "questions": [], "write_mode": "draft", "backend": "sample", "synthetic": true}
  - tool: desk_create_draft
    output: {"case_id": "c-3", "number": "CASE-000003", "state": "erp_draft_saved", "erp": {"doctype": "Sales Order", "name": "SAL-ORD-2000-00007", "docstatus": 0}}
---
## Prompt
I own CASE-000003 and I reviewed it. Save its ERP draft.

## Criteria
Saves the draft with the case's revision and digest, then says an ERP draft SAL-ORD-2000-00007 was saved with docstatus 0 and that a person still submits it. Never says the order is posted, booked, confirmed or submitted.
