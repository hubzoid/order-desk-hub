# Order & Invoice Desk: inbox records, reports and optional ERP

Proposed destination: `order-invoice-desk-hub/proposals/2026-10-20-inbox-records-and-optional-erp.md`.

Status: approval draft. This proposes implementation, not a claim of released capability. The qualification table, delegated setup authority, customer-owned Gmail OAuth client, storage authorities and owner self-approval below are proposed choices pending founder approval. Approval would supersede earlier ERP-first assumptions for this hub, while preserving B029 write controls and B042 code ownership. It adopts the [ready-hubs common contract](2026-10-20-ready-hubs-common-contract.md), B029, B037, B038 and B042. The Order & Invoice Desk remains the canonical owner of desk ERP code. Customer Desk retains a marked copy of its read parts. No fourth repository or shared desk package is introduced.

## Problem

The desired result is straightforward: connect Gmail, organize the company's orders and invoices, see useful reports and exports, and resolve exceptions without installing an ERP. ERP enrichment and approved posting should be an optional addition.

Today's desk cannot deliver that result. Its matching and proposal path requires ERP configuration and master-data lookup. The sample backend removes the need for a real ERP account but is still a fake ERP dependency. Parsing and source verification are reusable. The normalized record, review and reporting path must become independent of ERP matching.

Personalization is also narrower than it appears. Exact aliases, pack sizes and unit mappings are editable files. The prose policies describe behaviour implemented in code. Editing their wording does not implement arbitrary company rules. Setup should propose mappings and usable rules from selected examples, ask about ambiguity, and preserve reviewed choices for reuse.

The review baseline at `e08ea0c7` records 110 synthetic checks passing and live ERP qualification skipped. Reported reproductions include two distinct invoices each consuming the same remaining 60 units, a tax clarification field rejected by the answer path, and a post-submit `needs_review` result discarded while chat shows 276.6 against an ERP total of 2417.4. Implementation must reproduce these risks before claiming fixes.

## Why now

This is a flagship ready-made hub. A useful first result must depend on the builder's material and rules, followed by a saved correction that changes the next result. An ERP prerequisite prevents that journey for inbox-only users. Agent-led setup must also expose operational failures rather than defer them to manual UI troubleshooting.

The SDK already owns email intake, work items, extraction stations, operation recovery and approvals. B038 favours those components over another ingestion or workflow engine. B042 keeps business-specific implementation in this hub and limits SDK changes to demonstrated necessities. The complete scope is the acceptance boundary. Dates follow readiness.

## What should happen

### Connect and get a useful result

A builder asks their installed authoring assistant, Claude Code, Cowork or Codex, to set up the hub folder. The assistant checks prerequisites, explains model and connector costs and permissions, creates or updates the setup files, runs doctor checks, and shows the proposed configuration. Setup requires no manual Hubzoid web-app configuration. External OAuth consent, account selection and consequential approvals remain verified human handoffs. The assistant verifies completion and resumes. Its narrowly delegated setup credential may prepare files and activate authorized connectors. It cannot mint or widen grants, read or administer secret values, activate shared context or approve business actions. Those require a confirmation bound to a verified internal human and exact digest that the assistant cannot produce. Owner self-approval is allowed with the applicable grant. A missing secure non-delegable confirmation path is a must-have SDK dependency, not permission to approve through ordinary assistant CLI calls.

Reuse the SDK's `provider: email`, `transport: gmail` OAuth path with a customer-owned OAuth client. This scope does not create a Hubzoid public OAuth app. Credentials and refresh tokens stay in the SDK secret store, never Git. The current first cursor handles new mail only. Historical import is a required missing capability, not an existing Gmail feature claim. The builder selects an account, date range and Gmail query or labels, previews the import scope, then starts a paginated, resumable import. That approved account/query/date boundary limits authorization, including recovery. Show counts, progress, failures and source links. Use selected own material for qualification. Unrelated message bodies and attachments matched by the query are discarded promptly without durable import. Classified business evidence is retained only under an explicit approved policy. Store necessary receipts without unrelated mail content. Historical import and recurring intake use one ingestion path and receipt ledger keyed by verified hub/company, connected mailbox account, provider message identifier and attachment/part identifier. A Gmail thread, sender, contact or subject is not a document identity or authorization boundary. Preserve each source occurrence. A forwarded or separately received copy becomes a scoped business-document duplicate candidate using document references and content evidence, not a new accepted financial record or an automatic merge. Distinct business documents remain distinct even when they share an attachment or thread. Restore preserves ingestion receipts and posting fences. An expired cursor requires explicit recovery without silently widening the historical scope.

Mailbox access stays personal unless an administrator explicitly grants team access to selected records. A sender address or contact entry suggests identity but never proves company membership or grants access. Verify the connected contact capability and consent scopes before using it. Ambiguous customers, suppliers, senders and item identities become questions.

### Review records and reports without ERP

Each order or invoice becomes a versioned desk record with its original document, message and attachment identifiers, timestamps, source hash, extracted fields and field-level evidence. Preserve revisions, corrections, reviewer and rule versions. Store party, references, dates, currency, quantities, unit text, lines, taxes, totals and validation status where supported by evidence. Missing facts remain unknown. ERP links are optional fields.

Provide four complete baseline outputs:

| Output | Required behaviour |
|---|---|
| CSV register | One row per record with identifiers, dates, party, currency, totals, status and evidence link. Escape spreadsheet formula content safely. |
| Order summary | Counts, quantities by stated unit, and monetary totals by currency and selected period or party. Keep incompatible units separate. |
| Invoice totals | Totals and taxes grouped by currency and invoice-date period, with unresolved and excluded records disclosed. No implicit currency conversion. |
| Exception aging | Open exceptions by reason, owner, creation date and age, linked to the questions and source records. |

Money and quantities use Decimal, never binary float. A header/line/tax total mismatch or unsupported tax basis or rounding becomes an exception excluded from accepted totals. Default tolerance is an exact match at stated precision. A reviewed typed rule may define an alternative tolerance and supported rounding. Tax-inclusive or exclusive treatment must be evidenced, never inferred. Keep original quantities unchanged. Approved conversions appear only in a separately labelled converted column. Reports show filters, timezone, generation time and included revisions. Each report includes the latest accepted revision of each distinct business document once. A record is accepted by explicit reviewer confirmation or by deterministic acceptance rules the owner approved in the saved configuration; model confidence alone is insufficient. This permits routine automated processing while keeping ambiguous records separate. Unreviewed, conflicting, superseded and unresolved duplicate-candidate records remain visible and are excluded from accepted-document monetary totals. Unknown totals or currencies are never coerced to zero or added to a known-currency group. Preserve source date-only values as calendar dates. Period filters use document date with inclusive start and exclusive end. Missing dates appear in an undated exception group. Timestamp filters use the displayed selected timezone; exception age is elapsed time from exception creation to the displayed generation instant. Credits, cancellations and amended documents are separately labelled exceptions until an explicit supported rule defines netting; no automatic credit treatment is introduced. Provisional extraction previews are clearly distinguished from accepted-document totals. An invoice does not establish payment, and an order does not establish inventory availability. Paid status, stock and fulfilment require authoritative connected evidence.

### Learn the company's rules through reviewed files

During bootstrap, the builder selects representative documents, company rules and expected outputs. The installed authoring assistant analyses them once to propose identity aliases, item mappings, unit conversions, field conventions and supported typed policies, each with its source. Present conflicts and uncertain interpretations to the owner. Run the proposed configuration on separate holdout examples before activation. Save approved files and the validation result for recurring use.

Preserve the B037 single-installed-runtime default. Codex-only setup does not require a Claude subscription. Qualify the required authoring routes separately. Subsequent extraction may use the configured installed runtime. New ambiguity prompts a human question. A case answer affects that case until an authorized reviewed change makes it reusable. A chat or MCP correction becomes a proposed-change record in the SDK data root citing case and evidence. An authorized human activates the exact diff through the builder/update workspace, including when hosted source is read-only. Chat tools never write outside `<hub>/output/<session>/`. Missing proposed-change or activation support is a named must-have SDK dependency.

Typed rules have validated fields and supported deterministic behaviour. Unsupported policy prose is identified as requiring an explicit extension. Documents cannot generate executable code, tools or active configuration. Future mappings retain evidence, review and holdout validation instead of silently learning from inbox text.

### Add ERP when wanted

Connecting ERPNext adds authoritative master-data matching, prices, stock or receipt checks, and optional draft or submission actions. It never becomes a prerequisite for organizing records or reporting. Preserve per-line extraction evidence alongside ERP choices and optional links.

B029 applies per action: `off` hides writes, `draft` permits only qualified inert drafts, and explicitly enabled `approve` permits submission after a verified internal human with the scoped approval grant approves the exact current document. An external sender or service identity cannot approve. Immediately before dispatch, recompute the action digest under current rule/source revisions and provider state, and recheck current grants, ownership and ERP version. A changed digest or authority voids approval and requires a fresh preview/review. If the digest is unchanged, retain both revision identifiers. Reconcile unknown outcomes through the existing operation ledger. Receipt availability must account atomically for competing invoices and pending drafts. Read back ERP totals and status, retain `needs_review`, and show those authoritative values consistently in chat and reports.

## Scope and non-goals

This includes agent-led setup, real Gmail history and recurring intake, ERP-free records and reports, reviewed bootstrap and corrections, optional ERP, authorization, recovery, documentation and production qualification. Keep useful existing upload and channel paths and existing ERP safeguards.

It does not build an ERP, payment service, generic ETL platform, alternate chat product, or universal connector framework. It does not introduce arbitrary executable policy generation, automatic posting, or claim supported hosting, assistants or providers without qualification. Hubzoid's web app remains the single chat UI. Supported theme and layout customization and existing MCP/tool interfaces remain available.

### Architecture and planned work areas

| Work area | Proposed responsibility |
|---|---|
| `desk/documents.py`, `extract.py`, `store.py`, `service.py` | Reuse parsing and verification, separate normalized records from optional ERP proposals, persist evidence and revisions, add reports and explicit duplicate handling. |
| `desk/propose.py`, `policy.py`, `knowledge/` | Reviewed sourced mappings, typed policy validation, holdout evaluation and optional ERP enrichment. |
| `tools_local/`, `workflows/`, agent instructions | One domain service supplies decisions, record/rule revisions, evidence and authorization to chat, workflows and MCP. Hub instructions apply only where Hubzoid runs the agent, including delegated-agent MCP tools. External clients using raw domain tools own their generation. Live retrieval records policy revision and evidence identifiers, without promising identical future results. |
| `erp/` | Canonical B042 adapter, cumulative receipt protection, exact approvals, read-back and reconciliation. Maintain the marked Customer Desk read-copy boundary. |
| Setup, doctor, manifest and docs | Complete folder authoring for runtime/model, agent design, harness, interface/theme, connectors and knowledge, using supported configuration. |
| Minimal SDK proposals | Bounded historical Gmail import, non-delegable confirmations, proposed corrections/activation, verified setup handoffs and any demonstrated cross-surface grant gap. Reuse SDK facilities and libraries. Separate SDK PRs are dependencies and are never merged by the implementing agent. |

The builder sees the folder and saved files. Instructions, rules, mappings, tool/action policies, role grant bundles and connector definitions are file-authoritative. CLI/API/Console edits produce reviewed file revisions in an authorized writable workspace. Identities, account links, member assignments, secrets and approvals are SDK-store authoritative, inspectable/exportable through CLI/API with appropriate redaction and backed up with operational data. No setting has two writable authorities. Appearance follows the common file-authority contract. Mutable state respects `HUBZOID_DATA_DIR` and the SDK storage contract. Do not route state through chat tool file-write workarounds. Validate the SDK lock and correct the invalid `hubzoid secrets --apply` setup instruction.

### Permission matrix

Roles are grant bundles. Every tool/action also checks verified principal, company, mailbox or record scope, current grants and write mode. Administration does not imply business approval authority.

| Capability | Builder | Member | Administrator |
|---|---|---|---|
| Read records, evidence, reports and exports | Explicit data scopes | Explicit data scopes | Explicit data scopes |
| Claim and answer a case | Granted operator scopes | Granted operator scopes | Granted operator scopes |
| Propose shared rules/configuration | Scoped build grant | Off by default | Scoped build grant |
| Activate reviewed rules | Explicit review grant | Off by default | Explicit review grant |
| Bind accounts, secrets and access | Human own-account linking or explicit administration grant | Off by default | Human administration grant; no delegation of privileged confirmation |
| ERP draft or submit | Scoped action grant plus mode and required review | Off by default, individually grantable | Same action rules |

Enforce denial before reads, exports and writes across every surface. Scheduled jobs use a scoped service identity. Revocation invalidates pending approval and future access. Delegated assistant setup/runtime credentials cannot exercise human activation, access-administration, secret-administration or approval grants, even when their delegator holds them. Retrieved text cannot change identity or authorization.

## Open questions

Approval is requested for the proposed choices named above, including the finite real qualification table, owner self-approval, customer-owned Gmail OAuth and split file/SDK-store authority. Record the secure human-confirmation mechanism, exact historical-query semantics, contact scopes and runtime authentication per row. Cowork must use an existing supported helper if one is needed. An unavailable path returns for a scope decision rather than silently becoming optional. The owner's representative and holdout documents, authoritative identity sources, supported typed company rules and desired report period are deployment inputs. Unsupported rules stay visible as extension decisions. None of these gaps permits an ERP requirement for baseline records.

### Required real qualification table

Every row is required before this scope is complete. Each runs real Gmail history and recurring intake through all four ERP-free outputs and the reviewed correction journey, using selected own material. Each separately qualifies the optional live ERPNext draft, exact approval, submission and recovery journey. Both hosts pass the common hosting checklist, including access, persistence, callbacks, restart, schedules, sleep/wake, backup/restore and upgrades.

| Host | Execution installation | Setup authoring | Optional ERP qualification |
|---|---|---|---|
| Local | Claude only | Claude Code and separately Cowork | Real ERPNext draft, exact approval, submission and recovery |
| Local | Codex only | Codex | Same real ERPNext journey |
| Railway | Claude only | Claude Code | Same real ERPNext journey |
| Railway | Codex only | Codex | Same real ERPNext journey |
| Maritime | Claude only | Cowork | Same real ERPNext journey |
| Maritime | Codex only | Codex | Same real ERPNext journey |

Other matrix cells receive synthetic compatibility coverage only and are unsupported for promotion. Local first use defaults to the runtime's existing sign-in. Each row records its actual authentication type, permissions, costs and terms. No local login establishes free, unattended, shared or hosted subscription use. No paid fallback or resource provisioning is authorized by proposal approval. A blocked required row prevents completeness and returns for an explicit scope decision. Use immutable SDK pins for testing. Promotion requires a tagged qualified SDK release.

## Definition of done

| Verification | Observable acceptance |
|---|---|
| First-use rehearsal | Each required authoring/runtime row follows documented setup through verified consent, imports selected real material, produces all four outputs without ERP, saves a reviewed correction and demonstrates changed behaviour on a holdout record after restart. |
| Intake and storage | History pagination and overlapping polls produce one source occurrence per stable ingestion key. Tests cover retries, forwarded copies, multiple attachments, one document in two messages, separate accounts with colliding references, expired cursors, interrupted imports, malformed attachments, evidence preservation, custom data root and restore. Duplicate candidates need resolution or an explicitly approved deterministic rule before counting/posting. Recovery never widens approved account/query/date scope. |
| Report correctness | A separate reviewer derives expected values by hand from these rules, reviews and commits them before the implementation they test. Compare every output, export and displayed total independently to those expected values, not to one another. Golden fixtures cover all four outputs: two currencies, incompatible units, unknown amount/date/currency, corrected revision, duplicate candidates, a credit/cancellation and timezone boundary. Include header/line/tax disagreements, inclusive/exclusive taxes, unsupported rounding, exact default precision and reviewed tolerances. Every included revision is traceable and conversions never change original quantities. |
| Rules and permissions | Conflicts, uncertain identities, unit conversions, unsupported policies and injected instructions stop appropriately. For fixed fixtures, chat, workflows and raw MCP tools return identical revision/evidence identifiers and allow/deny decisions. Test every exposed action, queued revocation, assistant attempts to exercise human authority, and rule/source changes between preview and dispatch. Delegated-agent and external-client generation are distinguished. |
| ERP regression and qualification | Reproduce and fix concurrent 60-unit overbilling, rejected tax answers and discarded post-submit review/totals. Verify stale approvals, mode changes, unknown outcomes and live ERPNext behaviour. A skipped live run is an explicit blocker to live ERP claims. |
| Upgrade and rollback | Rehearse additive versioned schema migration preserving cases, proposals, rules, evidence, approvals and ERP links. Never re-post or recompute accepted work. Backup, stop intake and writes, restore compatible state and reconcile uncertain operations before resuming. |
| Release handoff | Focused checks then one appropriate broader regression pass, meaningful synthetic and real-provider evidence, documented skips and costs, source/SDK pins and independent review. Every required real row must pass. No critical timeout or skip counts as passing. |

Existing installations retain their optional ERP workflow without silent conversion. Production readiness requires the complete promised journey and recovery behaviour, not only the sample demonstration.
