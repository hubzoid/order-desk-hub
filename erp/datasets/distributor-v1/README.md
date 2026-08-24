# distributor-v1: a synthetic distributor

**SYNTHETIC DATA.** Synthetic Meridian Distribution is a fictional company. Every
name starts with "Synthetic", every email address uses a reserved `.example`
domain, phone numbers are in the reserved 555-01XX range, and there are no tax
ids, bank details or real records. Money is USD and times are UTC. Licence:
Apache-2.0, original data, made by `erp/datasets/generate.py` (never by a model).

## What is here

| Folder | Contents |
|---|---|
| `erp/` | ERPNext-shaped documents for every backend: company, warehouses, UOMs, price lists, 4 customers, 3 suppliers, 12 items, 24 item prices, stock bins, 6 sales orders, 4 purchase orders, 3 purchase receipts, 4 sales invoices, 3 purchase invoices, contacts. Draft, submitted and cancelled states, a disabled item, missing stock, two similar SKUs, an expired price, a contract price, a partial receipt and conflicting bill references are planted on purpose. |
| `messages/` | Intake envelopes (HUB-106 `IntakeEnvelope` v1): the twelve Order & Invoice Desk messages, the carton correction extra, the twenty Customer Desk requests, and adversarial companions for both desks. `trust` is `pending`: the synthetic importer derives it from `bindings.csv`, a message never asserts it. |
| `raw/` | The same messages as provider bytes: RFC 822 email and WhatsApp-style JSON. |
| `attachments/` | Original PDF, spreadsheet and image fixtures, each labelled synthetic. One PDF is truncated on purpose. |
| `print/` | Deterministic print PDFs of the four submitted sales invoices (for invoice copies). |
| `expected/order-invoice-desk.json` | The Order & Invoice Desk oracle: ten drafts and two questions for the twelve, plus the extras and adversarial cases. Fixture metadata, never model input. The Customer Desk owns its own oracle (`messages/customer-desk.index.json` only names each request's planted category). |
| `bindings.csv` | Which sender address or phone belongs to which customer or supplier, for the synthetic importer. |
| `manifest.json` | Schema version, seed, fixed clock and a SHA-256 for every file. |

## The twelve Order & Invoice Desk messages

Five orders (email body, PDF, spreadsheet, WhatsApp text, photo of a list), five
supplier bills that match their purchase order and receipt (PDF, spreadsheet,
email body, PDF, photo), one order with an ambiguous item ("Synthetic Hex Bolt
M8" matches two items) and one bill for more than was received (50 billed, 40
received). These counts are a fixture oracle, not a quality claim.
