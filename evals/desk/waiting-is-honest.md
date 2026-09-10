---
tags: [desk, waiting, scripted-tools]
expect_tools: [desk_case]
not_contains: ["has been submitted", "was submitted", "is submitted", "posted"]
fake_tools:
  - tool: desk_case
    output: {"case": "CASE-000005", "state": "waiting_approval", "kind": "purchase_invoice", "erp": {"doctype": "Purchase Invoice", "name": "ACC-PINV-2000-00004", "docstatus": 0}, "questions": [], "actions": []}
---
## Prompt
Has CASE-000005 gone into the ERP as a submitted invoice yet?

## Criteria
Says the invoice is an ERP draft (docstatus 0) waiting for a person's approval and is not submitted yet. Does not claim it was submitted.
