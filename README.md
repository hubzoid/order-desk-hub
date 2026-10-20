# Order & Invoice Desk

Connect Gmail, organize customer orders and supplier invoices, review exceptions, and get reports without an ERP. ERPNext is an optional addon for master-data checks and reviewed drafts/submission.

**Development build.** Deterministic and synthetic integration tests do not qualify a real mailbox, installed model login, live ERPNext or hosting provider. Current evidence and remaining checks are in [qualification](docs/qualification.md).

## Set up with your assistant

Use Claude Code, Cowork with supported local file/command access, or Codex in this hub folder:

> Read skills/setup/SKILL.md. Set up an inbox-only Order & Invoice Desk using my installed runtime. Help me select a bounded business-mail query or existing label and dates, inspect the proposed files and permissions, and connect my customer-owned Gmail OAuth client. Keep secrets out of chat. Show useful records and all four reports, resolve one exception, then propose a sourced reusable correction and test it on a separate example. Do not enable ERP writes or send email.

A business label is convenient, not required. The assistant can help select a bounded subject, attachment or exact-sender query. No manual Hubzoid settings UI or Gmail label creation is required. Google sign-in/consent and exact business-action approval remain human steps. Cowork needs an existing supported terminal/file bridge. If unavailable, show that gap and use the qualified builder checkout rather than claiming it works.

Prerequisites: Python 3.11+, the immutable tested Hubzoid SDK in `hub.lock.json`, and one installed signed-in Claude or Codex runtime. Install the exact SDK with `pip install -r requirements-sdk.txt`, then this hub with `pip install -e .`. Email text and CSV/XLSX work without Docling. PDFs/images need `pip install -e '.[parsing]'` and `hubzoid-desk parser prefetch`. Model authentication and OCR assets are separate. Hosted or unattended subscription use is qualified per combination, never inferred from local login.

[Setup recipe](skills/setup/SKILL.md) contains exact commands, the Gmail handoff, access grants, troubleshooting and the correction loop. [Inbox guide](docs/inbox-records.md) explains record review and report semantics.

## Four useful outputs

```bash
hubzoid-desk report . register --csv > orders-and-invoices.csv
hubzoid-desk report . orders --start 2026-10-01 --end 2026-11-01 --json
hubzoid-desk report . invoices --start 2026-10-01 --end 2026-11-01 --json
hubzoid-desk report . exceptions --json
```

In chat or through the same tools over MCP, ask “Show the invoice totals for October” or “Export the register”. The register exports safe CSV. Grouped reports return structured totals and evidence. Only the latest accepted revision contributes to financial totals. Unknowns, conflicting totals, duplicate candidates, unsupported credits/taxes/rounding and unreviewed records stay visible for review. Currencies and original units stay separate. An invoice is not proof of payment, and an order is not proof of stock.

Rules and aliases live in `knowledge/record-rules.md`, with sources in `knowledge/sources/`. The assistant proposes initial mappings from selected examples and your rules. The owner reviews the exact diff and a separate holdout before activation. A case correction stays case-only until explicitly saved as a shared rule. Only supported typed rules execute. Policy prose cannot generate code or activate itself.

## Optional ERPNext

Set `DESK_ERP_ENABLED: true` in the reviewed `hub.settings.yaml`, configure `connectors/erpnext.yaml`, and follow [ERPNext setup](docs/erpnext-setup.md). Existing deployments without the new setting retain their explicit ERP configuration. Turning on the addon does not post existing records or silently recompute accepted work. Existing unlinked cases can be explicitly rerun for ERP matching after review.

ERP writes have `off`, `draft` and `approve` modes. Drafts are inert docstatus-0 documents, and submission needs the verified internal requester's exact human approval. Every line retains its source. Current grants, rule revision, draft content and ERP version are rechecked at dispatch. Ambiguous outcomes must be reconciled before retry. Draft capacity checks are serialized across workers sharing this deployment's data root. Other independent ERP writers require the ERP's own concurrency validation.

The labelled fictional distributor sample remains available with `hubzoid-desk sample install .`, then the existing sample load flow. This explicitly enables the sample ERP addon. It is not evidence of a real ERP-free or live ERPNext deployment. Sample data uses USD and UTC.

## Files and operation

The agent is this folder: instructions, `agents/`, `tools_local/`, `workflows/`, connector definitions, rules, skills, examples and doctor checks. `desk/` owns records/reports/cases; `erp/` remains the canonical ERP adapter under B042. There is no fourth package or alternative chat UI. Supported models, harness instructions, knowledge and the existing web app's appearance remain customizable through reviewed files.

Mutable records, operations, sample ERP state and artifacts use the SDK data root, including `HUBZOID_DATA_DIR`. Back up that root with the approved source revision. Source mounted read-only is updated through an authorized builder checkout and redeploy. [Qualification and recovery](docs/qualification.md) records limits, rollback and real-provider gaps.

Apache-2.0. ERPNext is a separate application and none of its code is bundled here.

Historical import and recurring polling have separate scopes. The historical query/date range does not filter future Gmail polling. Recurring polling uses configured Gmail label IDs. Choose a dedicated business mailbox or existing business labels, or explicitly review whole-INBOX retention. Setup previews the actual ongoing mailbox/labels and preserves existing channel settings. No new Gmail label or provider UI step is required. Tokens are saved automatically by OAuth in a distinct channel-scoped SDK slot.
