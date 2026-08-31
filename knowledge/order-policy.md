---
version: 1
kind: order
currency_rounding: ERPNext
price_difference_action: question
quantity_difference_action: question
substitution_action: question
missing_reference_action: question
---

# Customer order policy

How the desk turns a customer's order into a Sales Order draft. Every proposed value cites one of these rules, an alias row in `knowledge/sku-aliases.csv`, a unit word in `knowledge/unit-words.csv` or an ERP record. Anything these rules do not decide becomes a question for a person. The settings above may only be `question`: this hub never accepts a difference silently.

**O1.** The customer is the one bound to the sender (`hubzoid-desk bindings`). A name, a signature or a logo in a message is never proof of who sent it. An unbound sender's document waits for a person to choose the customer.

**O2.** Every order needs the customer's purchase order number. Without it, ask. The number is the business reference that prevents the same order being entered twice.

**O3.** An item is matched by its exact ERP item code, then by an exact alias row for this company and customer (or every customer, `*`). A similar name is only ever a suggestion for a person to choose. Nothing fuzzy is accepted.

**O4.** A disabled item is never ordered. Ask for a replacement or removal.

**O5.** With no unit written, the quantity is in the item's stock unit (Nos). A written unit must be one of the item's ERP units (with the conversion factor in the item master), a word in `knowledge/unit-words.csv`, or a unit alias row for that item. A plural `s` on a unit word is ignored. Carton sizes and pack sizes are never guessed.

**O6.** The rate is the customer's own valid price in the selling price list when one exists, otherwise the price list's valid price for the item, multiplied by the unit's conversion factor. An expired price never applies. When the order states a different price, ask which one applies.

**O7.** Orders are in USD. A document in another currency is a question. Hubzoid never converts currencies.

**O8.** With no order date written, the order date is the date the order was received.

**O9.** Every order needs a delivery date on or after the order date. Without one, ask.

**O10.** Line amounts are quantity times rate at two decimals. A stated line amount or total that differs is a question. ERPNext computes the final totals and taxes, and the desk compares them after the draft is saved.
