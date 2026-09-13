---
tags: [desk, scripted-tools]
expect_tools: [desk_create_draft]
not_contains: ["has been posted", "was posted", "is now posted", "has been booked", "was booked", "order is confirmed"]
fake_tools:
  - tool: desk_case
    output: {"case": "CASE-000003", "case_id": "c-3", "state": "draft_ready", "revision": 5, "digest": "sha256:0f0bbf2c69b4eb5de0185dcd0953935aa3f1633a0270b75c74b09e059021a17c", "owner": "admin@localhost", "actions": ["create_draft", "release", "cancel"], "lines": [], "questions": []}
  - tool: desk_create_draft
    output: {"case_id": "c-3", "number": "CASE-000003", "state": "erp_draft_saved", "erp": {"doctype": "Sales Order", "name": "SAL-ORD-2000-00007", "docstatus": 0}}
---
## Prompt
I own CASE-000003 and I reviewed it. Save its ERP draft.

## Criteria
Saves the draft with the case's revision and digest, then says an ERP draft SAL-ORD-2000-00007 was saved with docstatus 0 and that a person still submits it. Never says the order is posted, booked, confirmed or submitted.
