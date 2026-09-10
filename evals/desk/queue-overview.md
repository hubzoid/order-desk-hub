---
tags: [desk, scripted-tools]
expect_tools: [desk_queue]
not_contains: ["posted", "booked"]
fake_tools:
  - tool: desk_queue
    output: {"cases": [
      {"number": "CASE-000003", "state": "needs_owner", "kind": "sales_order", "party": {"type": "Customer", "id": "SYN-CUST-002"}, "reference": "AH-3315", "total": "88.5", "owner": null, "questions": []},
      {"number": "CASE-000011", "state": "needs_clarification", "kind": "sales_order", "party": {"type": "Customer", "id": "SYN-CUST-002"}, "reference": "AH-3316", "total": "6.5", "owner": null,
       "questions": [{"code": "ambiguous_item", "message": "Line 1: \"Synthetic Hex Bolt M8\" matches 2 items. Which one is it?", "field": "lines[1].item_code"}]}],
      "counts": {"needs_owner": 1, "needs_clarification": 1}}
---
## Prompt
What is in the desk queue?

## Criteria
Lists both cases with their state, party, reference and total exactly as the tool returned them. Says CASE-000011 needs an answer about which bolt it is. Invents no other case, number or amount, and does not call anything posted or submitted.
