"""The Order & Invoice Desk: documents in, checked ERP drafts out.

    intake (email, WhatsApp, upload, the sample loader)
      -> triage work item: parse -> extract (station) -> match and validate
      -> a desk case with a proposal, every value tied to its source and rule
      -> an operator claims it -> a draft work item saves an inert ERP draft
      -> in approve mode a submit work item waits for that person's approval
         of the exact draft, then submits it

Extraction is a model's structured output. Everything that decides money,
quantities, matches, duplicates, permissions and writes is deterministic code
here. Writes go through the SDK operation ledger (`hub.operation.run`) and the
`erp` module's three-call contract.
"""

__version__ = "0.1.0"
