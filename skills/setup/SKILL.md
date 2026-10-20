---
name: setup-order-desk
description: Set up this folder for Gmail orders/invoices and four reports without ERP, then review reusable sourced mappings.
---
# Setup Order & Invoice Desk

Read README.md and docs/inbox-records.md. Work as an authorized local host operator in a writable builder checkout. Host ownership is a trust boundary. This recipe uses existing SDK identity and human approval facilities. It does not claim to constrain a hostile OS administrator or implement non-delegable setup tokens. That generic hardening is deferred.

1. Confirm the selected company, installed `claude-local` or `codex-local` runtime, parser needs and customer-owned Google OAuth client. Run `hubzoid stations doctor .` and inspect `hub.lock.json`. No additional vendor subscription or silent paid API fallback. Never request raw secret values in chat.
2. Preview setup:

   ```bash
   hubzoid-desk setup . --company 'My Company' --runtime codex-local --mailbox ops@example.org --client-id CUSTOMER_OAUTH_CLIENT_ID --client-secret-ref env:GMAIL_CLIENT_SECRET --gmail-label EXISTING_BUSINESS_LABEL_ID --json
   ```

   Review the exact file diff and the separate ongoing_scope. Use repeated `--gmail-label Label_ID` options for existing business labels, or a dedicated business mailbox. Default INBOX authorizes ongoing intake of all newly admitted INBOX mail, with selected source content retained. The historical keyword query below does not filter future polling. If choosing INBOX, the person must explicitly review that whole-INBOX scope. No label creation is required.

   Review the exact file diff. Repeat with `--apply sha256:PREVIEW_DIGEST`. Choose `claude-local` for a Claude-only installation. Setup writes inbox mode, the extraction station/runtime default and the Gmail channel definition. It does not activate shared rules, create grants, store secret values, approve ERP writes or contact Google. Existing settings are shown before changes. An existing custom company rule file is preserved. Validate it with `hubzoid-desk rules check .`.
   Choose exactly one existing label ID for recurring intake. A nonexistent label will not create one: verify it against the person's mailbox. Alternatively a person explicitly approves retention of every newly admitted INBOX message and the command uses `--whole-inbox` instead of `--gmail-label`. Historical keyword queries never restrict recurring polling. Multiple labels are rejected because current polling/import semantics differ.

3. The credential administrator supplies the referenced client secret using `hubzoid secrets set . --ref secret:hub/HUB/gmail-client --stdin` or the declared operator environment source. Match `--client-secret-ref` to that reference. There is no `secrets --apply` option. Do not echo or capture its input. OAuth saves tokens automatically in `secret:channel/HUB/CHANNEL/gmail-oauth`, separate for every mail channel. Never set that token slot with the CLI. Client secrets may still use a hub secret reference. Tokens live in the SDK secret store. Configure the SDK's public URL and Google callback according to its email setup docs.
4. Validate and activate the reviewed channel:

   ```bash
   hubzoid channels validate . --file channels/orders-mail.yaml --json
   hubzoid channels apply . --file channels/orders-mail.yaml --expected-revision absent --json
   hubzoid channels email connect . orders-mail --mode draft --json
   ```

   For an existing connection use its actual revision, never `absent` or blind overwrite. A person opens the returned Google consent URL in their account and completes MFA/consent. Then verify `hubzoid channels email status . orders-mail --json`. The connector's draft mode requests Gmail readonly plus compose scopes. Compose also permits sending at Google, but this hub offers no automatic email send. Explain the returned scopes. Do not claim read-only OAuth merely because this desk only reads.
5. Review exact allowed sender addresses in the channel's sender policy. Unknown/unverified senders are quarantined. Use `hubzoid intake list . --channel orders-mail --json`, inspect each selected item and release only legitimate business material with `hubzoid intake release . ITEM --reason 'Reviewed business source'`. A sender/contact never becomes a member. The authorized administrator grants the human mailbox owner `desk_view`, `desk_operate`, `wf_order_invoice_intake` and the `source_grant` returned by setup. Each mailbox gets a separate scope, including its actual account. Do not grant those scopes to external senders. Generic admin membership does not grant source data access.
6. Select an existing business label or a bounded business query, with explicit UTC date range and message limit. A label is optional. An assistant-reviewed sender/subject/attachment query avoids manual Gmail label setup. Do not default to the entire personal inbox or a nonexistent label. All selected source evidence is retained under the chosen policy; generic intake does not discard unrelated mail automatically. Review the scope before import:

   ```bash
   hubzoid channels email import-history . orders-mail --query 'has:attachment subject:invoice' --after 2026-10-01 --before 2026-10-21 --limit 100 --json
   ```

   Repeat the exact command with `--apply sha256:SCOPE_DIGEST`, then rerun until `state: complete` or the explicit limit is reached. Each invocation processes one bounded page. The SDK checkpoints and uses normal intake receipts, so overlap with recurring polling is idempotent. Expired paging needs `--restart` with the same approved scope, never wider dates/query/account. This command requires the root-owned bounded-Gmail-history SDK change recorded in qualification. Do not claim it exists on an older release.
7. Start with `HUBZOID_SCHEDULES=1 hubzoid run .`, keeping the process available while imports/workflows run. For hosted operation validate persistent data, OAuth callbacks, exact model authentication, schedules and sleep/wake separately. Use `hubzoid-desk doctor . --json`, `hubzoid-desk list . --json`, then show a case and its evidence. Resolve questions with `desk_review_record` or `hubzoid-desk review-record . CASE --expected-revision RECORD_REVISION --changes '{"tax_basis":"exclusive"}' --accept --json`. Accept only source-validated records with no remaining exceptions.
8. Produce all four reports using README commands. In the web app/MCP use `desk_report`. Export only the register as CSV. Grouped summaries remain structured JSON. Show unresolved/excluded records rather than implying missing money is zero or a received invoice was paid.
9. Bootstrap once from selected representative documents, explicit company rules and an example of a good result. Preserve source material inside `knowledge/sources/`. Propose exact aliases/conversions and supported typed policies in `knowledge/record-rules.md`. Show uncertain mappings as questions. Test separate holdout documents with `hubzoid-desk preview . FILE --json` before activation. On writable source, `desk_propose_record_rules` stores a pending SDK Hub-change proposal, never activates it. On read-only source it returns a readable builder diff and explicitly stores no pending proposal. The authorized human uses existing Hub-change review/approval in their own session, or reviews/commits files in the builder checkout. On read-only hosting, redeploy that approved checkout. Save one correction and verify the next result after restart. Unsupported prose needs scoped engineering, not generated code.

Troubleshoot missing records by checking connection status, bounded import progress, sender quarantine, intake/work-item state, enabled source and runtime login in that order. Empty reports may mean every record is still unreviewed or excepted. Do not enable ERP merely to fix an empty report. `desk_health` and the exceptions report explain the queue.
