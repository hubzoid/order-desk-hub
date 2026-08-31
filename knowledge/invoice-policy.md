---
version: 1
kind: invoice
currency_rounding: ERPNext
price_difference_action: question
quantity_difference_action: question
substitution_action: question
missing_reference_action: question
---

# Supplier invoice policy

How the desk turns a supplier's invoice into a Purchase Invoice draft, matched to what we ordered and received. Every proposed value cites one of these rules, an alias row or an ERP record. Anything these rules do not decide becomes a question. This hub never pays, never changes bank details and never edits a supplier.

**I1.** The invoice must be billed to Synthetic Meridian Distribution. An invoice addressed to any other company is never booked here. The supplier is the one bound to the sender, never a name in the message.

**I2.** Every invoice needs the supplier's invoice number, the invoice date and the due date. The invoice number is the business reference that prevents the same bill being booked twice. A bill number already booked in the ERP (draft or submitted) is a duplicate.

**I3.** The posting date is the invoice date.

**I4.** The invoice must cite our purchase order, and every line must match one row of it by exact item code (or an exact alias row for this supplier). A line that matches no row, or more than one, is a question.

**I5.** The rate must equal the purchase order's rate. A different rate is a question.

**I6.** Line amounts are quantity times rate at two decimals and the total is their sum. A stated amount or total that differs is a question. A tax amount on the invoice is a question: the desk sends no tax lines, and ERPNext applies its own tax template.

**I7.** Only goods received can be billed. A line bills receipt rows of its purchase order row, and never more than was received and not yet billed. Drafts and submitted bills both count as billed, cancelled ones do not. Billing more than that is a question.

**I8.** Credit notes, returns, payments and bank detail changes are not desk operations. They are handed to a person in the ERP.
