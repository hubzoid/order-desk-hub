# Writing the desk's rules

The desk decides nothing the files do not say. When something is not covered, it asks. These files are how you teach it, and every proposal cites the rule or row it used.

## Record rules (no ERP needed)

`knowledge/record-rules.md` holds the rules for organizing records without an ERP: aliases, unit conversions, tolerance, rounding and the default tax basis, each citing its source. Check them with `hubzoid-desk rules check .`. In chat, `desk_propose_record_rules` proposes a sourced change as a reviewed Hub change; it never activates one. See [how records and reports work](inbox-records.md).

## ERP addon policies

`knowledge/order-policy.md` and `knowledge/invoice-policy.md` start with frontmatter that the desk validates:

```yaml
---
version: 1
kind: order                       # or invoice
currency_rounding: ERPNext        # ERPNext computes the final totals
price_difference_action: question
quantity_difference_action: question
substitution_action: question
missing_reference_action: question
---
```

The four actions can only be `question` in this hub: a difference is never accepted silently. The body holds the numbered rules (`**O1.**` to `**O10.**`, `**I1.**` to `**I8.**`) in plain English. The code implements them and every proposal cites them by number, so keep the numbers stable when you edit the wording.

## Alias rows

`knowledge/sku-aliases.csv`:

```csv
company,party,item_alias,item_code,source_uom,target_uom,factor,source_ref
Synthetic Meridian Distribution,SYN-CUST-002,AH-B840,SYN-ITEM-009,,,,knowledge/sources/alpine-hardware-part-numbers.md
Synthetic Meridian Distribution,*,SYN-ITEM-001,SYN-ITEM-001,carton,Nos,12,knowledge/sources/carton-rule.md
```

- An **item alias** (no units): a customer's or supplier's own code or exact description for one of your items. `party` is the customer or supplier id, or `*` for everyone.
- A **unit alias** (`source_uom`, `target_uom`, `factor`): "a carton of SYN-ITEM-001 is 12 Nos". The source quantity times the factor must give a whole number of the target unit.
- `source_ref` points at the evidence, usually a note in `knowledge/sources/` quoting who said it and when.
- Matching is exact after normalising case, spacing and Unicode forms. A plural `s` on a unit word is ignored. Nothing fuzzy.
- Two rows that map the same alias to different targets are refused (`alias_conflict`, with the row numbers).

## Unit words

`knowledge/unit-words.csv` maps words in documents to ERP units (`boxes` to `Box`, `pcs` to `Nos`). The item master's own conversion factors apply.

## The correction loop

1. A case asks a question you want answered for good, for example "How many Nos are in one carton?".
2. Write the source note in `knowledge/sources/`, add the row, then check it: `hubzoid-desk rules check .`.
3. Rerun the document: `hubzoid-desk rerun . CASE --reason "carton means twelve"`. A new proposal revision appears. Only the lines the rule touches change, and the earlier revision is kept.
4. The rule is a file, so it applies to every later document and survives restarts. Review it like any change to the hub (a pull request, or a Hubzoid hub change proposal).

A case with an ERP draft is never re-proposed: change the draft in ERPNext instead.
