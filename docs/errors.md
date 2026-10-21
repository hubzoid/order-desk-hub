# Error and question reference

Every refusal has a stable code, the same in chat, the CLI (`--json` prints `{"detail": {code, message, ...}}`) and the case page. CLI exit codes: 0 done, 1 failed, 2 invalid, 3 denied, 4 conflict, 5 unavailable, 6 waiting or uncertain.

## Desk actions

| Code | Meaning | Next step |
|---|---|---|
| `permission_denied` | You lack the desk grant for this, or the grant to start the desk's workflow | An administrator runs `hubzoid grant EMAIL desk_operate .` (or `desk_view`, `desk_submit`, `desk_manage`). Saving drafts and asking to submit also need `wf_order_invoice_case`, and uploads `wf_order_invoice_intake`: the error names the grant |
| `not_owner`, `owner_conflict` | Someone else owns the case, or claimed it first | Ask them, or an administrator reassigns it |
| `revision_conflict` | The case changed since you loaded it | Reload it and try again |
| `invalid_state` | The action does not fit the case's state | See the state table in the operator guide |
| `stale_preview` | The proposal or ERP draft changed since you reviewed it | Review the current one and confirm its digest |
| `review_required` | ERPNext computed different values for the draft | Review the draft in ERPNext before asking to submit |
| `write_mode_off`, `write_mode_not_approve` | `erp.submit` is off, or not approve, when asking or when the write was about to be sent | Change `work-items.yaml` when the ERP is ready (doctor), then ask again |
| `receipt_capacity_changed` | Another bill took the received quantity after this proposal was made | Rerun the case, so it proposes against the ERP as it is now |
| `operation_unresolved` | An ERP write's outcome is unknown | Reconcile first |
| `decide_as_requester` | The CLI acts for a person only in local owner mode | Use the chat or the Console as yourself |
| `source_disabled` | The channel is not in `DESK_SOURCES` | Add it to `hub.settings.yaml` |

## Reading documents (case errors)

| Code | Meaning | Next step |
|---|---|---|
| `file_unsupported` | Not a supported type, encrypted, or with macros | Send a PDF, PNG, JPEG, XLSX, CSV or the text in the message |
| `file_too_large` | Over `DESK_MAX_FILE_BYTES` or `DESK_MAX_PAGES` | Send a smaller document |
| `parse_timeout` | Longer than `DESK_PARSE_TIMEOUT_SECONDS` | Split the document. The first image after install is slow until `hubzoid-desk parser prefetch` has run |
| `parse_failed` | Damaged or unreadable | Ask the sender for a new copy |
| `parser_unavailable` | Docling or its model assets are missing | `pip install -e '.[parsing]'` in the hub folder and `hubzoid-desk parser prefetch` |
| `extraction_failed` | The extractor station did not finish (often the model login) | `hubzoid stations doctor .`, then restart the intake work item |
| `too_many_lines` | Over `DESK_MAX_LINES` per draft | Split the order |
| `policy_invalid`, `alias_conflict` | A rule file is invalid, or two alias rows disagree | Fix the file and line named, `hubzoid-desk rules check .`, then rerun |

## Questions on a case

| Code | The desk asks |
|---|---|
| `party_unknown` | Which customer or supplier sent this (the sender is not bound) |
| `kind_mismatch` | Whether it is an order or an invoice (the sender and document disagree) |
| `missing_reference`, `missing_delivery_date`, `missing_bill_date`, `missing_due_date` | The missing value |
| `item_unknown`, `ambiguous_item`, `item_disabled` | Which item a line is, from the listed suggestions |
| `unit_unknown` | How many stock units one of the written unit is |
| `price_missing`, `price_ambiguous`, `price_difference` | Which rate applies |
| `quantity_missing`, `quantity_not_whole`, `amount_mismatch`, `total_mismatch` | The right quantity or amount |
| `wrong_company`, `bill_to_missing` | Whether the invoice is really billed to this company |
| `missing_purchase_order`, `purchase_order_unknown`, `po_line_missing`, `po_line_ambiguous` | Which purchase order row a bill line is for |
| `receipt_missing`, `receipt_quantity_exceeded` | Whether to bill only what was received |
| `tax_present` | To check the ERP's tax template (the desk sends no tax lines) |
| `line_not_extracted` | Whether a source element that looks like a line (it names an ERP item, reads "N x ...", or sits among the order's rows) is one. Add it (`lines[N].source=E4` with its item and quantity) or answer `ack.uncovered.E4=not_a_line` |
| `line_repeated` | Whether a line that repeats another from the same source text is really listed twice (`lines[N].include=yes` or `no`) |
| `attachment_unreadable` | Whether anything is missing when one attachment could not be read (`ack.unreadable.N=nothing_missing`, or cancel and ask for a readable copy) |
| `not_our_invoice` | A person answered that the invoice is not billed to this company. Cancel the case |
| `rate_missing` | The rate per unit when the invoice gives none and the order is priced in another unit |
| `currency_mismatch`, `delivery_before_order`, `due_before_bill`, `duplicate_conflict`, `not_supported`, `no_lines` | As named |

## ERP connection and writes

| Code | Meaning | Next step |
|---|---|---|
| `config_invalid`, `config_missing` | `connectors/erpnext.yaml` is invalid or absent | Fix the field named, then `hubzoid-erp configure` |
| `secret_missing`, `erp_auth_failed` | The credential is unset or refused | A credential administrator sets or rotates it with `hubzoid secrets set` |
| `erp_permission_denied` | The ERP user lacks a right | Grant the narrow permission doctor names |
| `erp_unavailable`, `erp_rate_limited` | The site did not answer, or is limiting | Reads retry. Try again later |
| `protocol_error` | Not a Frappe answer (a redirect, a proxy page, oversized) | Check `site_url` |
| `erp_writes_disabled` | `writes_enabled: false` | Enable after doctor shows ready_write |
| `invalid_payload`, `company_denied`, `erp_validation_failed` | The draft payload was refused, before or by ERPNext | Correct the proposal |
| `duplicate_found` | The business reference already exists in ERPNext | Review the existing document |
| `operation_conflict`, `reconcile_conflict` | The marker points at a different document, or at several | An administrator investigates in ERPNext |
| `draft_not_inert` | ERPNext saved a "draft" with docstatus other than 0 | Critical: writes must stay off until the site's hooks are checked. Nothing is cancelled automatically |
| `erp_schema_unsupported` | The custom field is missing or not unique. The desk never inserts without it, and never retries a lost create without it | Run the bootstrap |
| `erp_write_ambiguous` | The write may have applied | Reconcile, never retry blindly |
| `approval_stale`, `already_submitted`, `submit_not_allowed` | The approved draft changed, is already submitted, or a Workflow governs it | Review and approve again, or submit in ERPNext |
| `sample_modified` | Local sample files differ from the dataset | Move them aside to reinstall |
