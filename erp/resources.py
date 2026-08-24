"""The typed, allowlisted ERP resources both desks read.

Each resource names its Frappe DocType, the only fields a read returns, the
child table rows it keeps (with their ERP row IDs), the party field that
`party_scope` enforces, and the filters a caller may use. Anything not listed
here (addresses, bank details, unrelated custom fields) is never returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Resource:
    name: str
    doctype: str
    fields: tuple[str, ...]
    child_table: str | None = None
    child_fields: tuple[str, ...] = ()
    party_field: str | None = None          # customer or supplier, for party_scope
    party_kind: str | None = None           # "customer" | "supplier"
    company_field: str | None = None        # transactional records carry a company
    warehouse_field: str | None = None      # Bin is limited to the allowed warehouses
    filters: tuple[str, ...] = ()           # exact-match filters a caller may use
    search_field: str | None = None         # case-insensitive name search
    date_field: str | None = None           # date-range filters
    extra_children: dict = field(default_factory=dict)   # other child tables kept: {table: fields}


_TOTALS = ("total_qty", "total", "net_total", "total_taxes_and_charges", "grand_total", "rounded_total")

RESOURCES: dict[str, Resource] = {r.name: r for r in (
    Resource("customers", "Customer",
             ("customer_name", "disabled", "default_currency", "default_price_list"),
             party_field="name", party_kind="customer", filters=("disabled",), search_field="customer_name"),
    Resource("suppliers", "Supplier",
             ("supplier_name", "disabled", "default_currency"),
             party_field="name", party_kind="supplier", filters=("disabled",), search_field="supplier_name"),
    Resource("items", "Item",
             ("item_code", "item_name", "stock_uom", "disabled", "is_stock_item", "description"),
             child_table="uoms", child_fields=("uom", "conversion_factor"),
             filters=("disabled", "item_code"), search_field="item_name"),
    Resource("prices", "Item Price",
             ("item_code", "price_list", "currency", "price_list_rate", "uom", "customer", "supplier",
              "valid_from", "valid_upto", "selling", "buying"),
             filters=("item_code", "price_list", "customer", "supplier", "uom")),
    Resource("availability", "Bin",
             ("item_code", "warehouse", "actual_qty", "reserved_qty", "projected_qty"),
             warehouse_field="warehouse", filters=("item_code", "warehouse")),
    Resource("sales_orders", "Sales Order",
             ("customer", "company", "transaction_date", "delivery_date", "po_no", "docstatus", "status",
              "currency", *_TOTALS, "per_delivered", "per_billed", "delivery_status", "billing_status",
              "selling_price_list", "set_warehouse", "custom_hubzoid_operation_id"),
             child_table="items",
             child_fields=("item_code", "item_name", "qty", "delivered_qty", "uom", "conversion_factor",
                           "stock_qty", "rate", "amount", "delivery_date", "warehouse"),
             party_field="customer", party_kind="customer", company_field="company",
             filters=("customer", "po_no", "docstatus", "status", "custom_hubzoid_operation_id"),
             date_field="transaction_date"),
    Resource("purchase_orders", "Purchase Order",
             ("supplier", "company", "transaction_date", "schedule_date", "docstatus", "status", "currency",
              *_TOTALS, "per_received", "per_billed"),
             child_table="items",
             child_fields=("item_code", "item_name", "qty", "received_qty", "uom", "conversion_factor",
                           "stock_qty", "rate", "amount", "schedule_date", "warehouse"),
             party_field="supplier", party_kind="supplier", company_field="company",
             filters=("supplier", "docstatus", "status"), date_field="transaction_date"),
    Resource("purchase_receipts", "Purchase Receipt",
             ("supplier", "company", "posting_date", "docstatus", "status", "currency", *_TOTALS),
             child_table="items",
             child_fields=("item_code", "item_name", "qty", "received_qty", "rejected_qty", "uom",
                           "conversion_factor", "stock_qty", "rate", "amount", "purchase_order",
                           "purchase_order_item", "warehouse"),
             party_field="supplier", party_kind="supplier", company_field="company",
             filters=("supplier", "docstatus", "status"), date_field="posting_date"),
    Resource("sales_invoices", "Sales Invoice",
             ("customer", "company", "po_no", "is_return", "posting_date", "due_date", "docstatus", "status",
              "currency", "outstanding_amount", *_TOTALS),
             child_table="items",
             child_fields=("item_code", "item_name", "qty", "uom", "rate", "amount", "sales_order",
                           "so_detail"),
             party_field="customer", party_kind="customer", company_field="company",
             filters=("customer", "docstatus", "status", "po_no", "is_return"), date_field="posting_date"),
    Resource("purchase_invoices", "Purchase Invoice",
             ("supplier", "company", "bill_no", "bill_date", "posting_date", "due_date", "docstatus", "status",
              "currency", "outstanding_amount", *_TOTALS, "buying_price_list", "custom_hubzoid_operation_id"),
             child_table="items",
             child_fields=("item_code", "item_name", "qty", "uom", "conversion_factor", "stock_qty", "rate",
                           "amount", "purchase_order", "po_detail", "purchase_receipt", "pr_detail",
                           "warehouse"),
             party_field="supplier", party_kind="supplier", company_field="company",
             filters=("supplier", "bill_no", "docstatus", "status", "custom_hubzoid_operation_id"),
             date_field="posting_date"),
)}

BY_DOCTYPE = {r.doctype: r for r in RESOURCES.values()}

#: Money and quantity fields: always Decimal strings in results, never floats.
DECIMAL_FIELDS = frozenset({
    "qty", "delivered_qty", "received_qty", "rejected_qty", "stock_qty", "conversion_factor", "rate", "amount",
    "price_list_rate", "actual_qty", "reserved_qty", "projected_qty", "outstanding_amount", "per_delivered",
    "per_billed", "per_received", *_TOTALS,
})
INT_FIELDS = frozenset({"docstatus", "disabled", "is_stock_item", "is_return", "selling", "buying"})
DATE_FIELDS = frozenset({"transaction_date", "delivery_date", "schedule_date", "posting_date", "due_date",
                         "bill_date", "valid_from", "valid_upto"})

#: Writable kinds: the only two documents a desk may draft.
WRITE_KINDS = {"sales_order": "Sales Order", "purchase_invoice": "Purchase Invoice"}


def get(name: str) -> Resource:
    from .errors import ERPError

    resource = RESOURCES.get(name)
    if resource is None:
        raise ERPError("unknown_resource", f"{name!r} is not an ERP resource here. Resources: "
                       f"{', '.join(sorted(RESOURCES))}.", status=400)
    return resource
