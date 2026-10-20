# Order Desk implementation plan

User authorized implementation and PR preparation on 20 October 2026. This authorization supersedes the local skill's merged-proposal and repeat-plan gates. The root coordinator may create exact review snapshot commits after inspection so the independent Claude review addresses stable bytes. Root creates the unmerged implementation PR only after review findings and required checks are handled. This child agent makes no implementation commits or PRs.

1. `desk/records.py`, `desk/reports.py`, `desk/store.py`: additive normalized record revisions with retained extraction/evidence, deterministic Decimal validation and duplicate candidates. Implement CSV register, order summary, invoice totals and exception aging from independently committed expected fixtures. Existing ERP case/proposal/link tables remain intact.
2. `desk/service.py`, `desk/flow.py`: normalize every verified extraction, then optionally enrich through existing ERP path. Fresh inbox setup is ERP-free. Existing explicitly configured installations retain ERP behavior. Wire authorized record review, corrections, reports and exports into the same service for CLI/tools/workflows. Preserve identity, owner and revision checks.
3. `desk/setup.py`, `desk/cli.py`, `tools_local/order_invoice.py`, runtime `AGENTS.md`, setup/operator docs and configuration: real folder-led setup with runtime choice and Gmail channel definitions, reviewed bootstrap through SDK hub changes, readable records/reports and optional ERP tools. Never collect secrets or approve business actions in setup. Reuse root-owned SDK historical Gmail hook.
4. `desk/paths.py`, `erp/service.py`, `desk/doctor.py`, lock validation: custom data-root support, optional ERP diagnostics, selected-runtime diagnostics and accurate SDK prerequisites.
5. `desk/propose.py`, `desk/service.py`, `desk/flow.py`, `erp/write.py`: reproduce tax-answer failure, competing-invoice capacity and lost post-submit review/totals, then targeted regression fixes. Notify root of canonical ERP read-copy changes.
6. Tests and handoff: focused financial/record/access/setup/ERP regressions, existing deterministic suite, engine checks as appropriate, doctor. Report all real-provider, installed-runtime and hosted qualification gaps honestly. No live mailbox/ERP writes or paid resources.

SDK dependencies: historical bounded paginated Gmail import through existing poller/intake receipts (root-owned), generic delegated setup identity is deferred hardening. Current setup uses trusted-host authoring and existing human approval cards. Read-only source updates use an authorized builder checkout/redeploy. SDK code is outside this worktree's ownership.

Financial expected values are derived and committed by root independently before reporting implementation. Test fixtures cover multiple currencies/units, unknowns, corrected revisions, duplicate candidates, credits, invalid totals/taxes/rounding, dates and exception ages. No self-derived report output is accepted as its own oracle.

Blast radius: desk intake and presentation, optional ERP rechecks and canonical ERP data paths. No new orchestration engine, generic ETL framework, runtime adapter, package or alternate UI.

## Implementation evidence before independent review

- Targeted records/reports/setup/ERP/rule-activation checks: 19 passed (2.87 s). Expected financial values were committed independently in c58ba43 before the reporting code.
- Broader deterministic suite: 120 passed, 1 skipped, 9 deselected (24.21 s). The skip is live ERPNext without a disposable configured site; the 9 engine cases were then run explicitly: 9 passed (60.03 s). Optional real-model/Docling/live-provider qualification remains outstanding.
- Engine fixtures explicitly enable the sample ERP addon rather than changing the inbox-only production default; child processes preserve the selected SDK.
- Doctor reports ok with the exact SDK pin and valid ERP-free rules. It warns that HUBZOID_SCHEDULES must be set and reports that no Gmail intake is connected in this checkout. No model/login/provider qualification is implied.
- Initial validation failures were environment/fixture setup: one run used an environment missing reportlab; a sample-engine fixture copied the new ERP-off setting. The correct environment and explicit sample fixture passed the checks above.
- Independent Claude Opus 5.5 will review this entire hub patch plus the bounded Gmail SDK dependency, with author dispositions below.

## Post-review validation

Independent Claude Opus 5.5 reviewed the entire hub implementation at 94a0bba plus SDK f99a224. Necessary author fixes are documented in docs/review-dispositions-2026-10-20.md. Final model-free suite: 181 passed, one live-ERP skip, 83.76 seconds, including all nine local workflow-engine cases. Focused setup/finance/access/SDK/export checks passed; actual signed chat CSV export is covered. Canonical ERP identity preservation is committed separately as a3c3993135ebffa2c291dac3a7780be46f7bf796 and mirrored by Customer Desk. SDK dependency: https://github.com/hubzoid/hubzoid/pull/109. No merge or real-provider readiness is claimed.

The durable invoice-capacity fence and uncertain-write recovery are consequential financial changes. Under the B034 exception for a risky fix, one narrow follow-up review covers those changes after these author fixes; this does not reopen the full implementation or optional scope.
