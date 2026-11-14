"""Stock, pending orders, purchase and accounts tools. All read-only and permission checked."""
import frappe
from frappe.utils import date_diff, nowdate

NOT_PENDING_SO = ("Closed", "Completed", "Cancelled", "On Hold")
NOT_PENDING_PO = ("Closed", "Completed", "Cancelled", "On Hold", "Delivered")


def _n(v, d, lo, hi):
	return max(lo, min(int(v or d), hi))


def _resolve_item(text):
	t = (text or "").strip()
	rows = frappe.get_list(
		"Item", filters={"disabled": 0},
		or_filters={"name": ["like", f"%{t}%"], "item_name": ["like", f"%{t}%"]},
		fields=["name", "item_name", "stock_uom"], limit=8,
	)
	if not rows:
		return None, {"error": f"No item found matching '{t}'"}
	exact = [r for r in rows if t.lower() in ((r.name or "").lower(), (r.item_name or "").lower())]
	if len(exact) == 1:
		return exact[0], None
	if len(rows) > 1:
		return None, {
			"error": "Multiple items matched, ask the user to confirm which one",
			"matches": [f"{r.item_name} ({r.name})" for r in rows],
		}
	return rows[0], None


def _resolve_party(doctype, name_field, text):
	t = (text or "").strip()
	rows = frappe.get_list(
		doctype, or_filters={"name": ["like", f"%{t}%"], name_field: ["like", f"%{t}%"]},
		fields=["name", name_field], limit=5,
	)
	if not rows:
		return None, {"error": f"No {doctype.lower()} found matching '{t}'"}
	if len(rows) > 1:
		return None, {
			"error": f"Multiple {doctype.lower()}s matched, ask the user to confirm which one",
			"matches": [r[name_field] for r in rows],
		}
	return rows[0].name, None


BIZ_TOOLS = [
	{
		"name": "get_item_stock",
		"description": "Current stock of an item: total and warehouse-wise actual qty, reserved qty (for sales orders), incoming qty (on purchase orders) and projected qty.",
		"parameters": {"type": "object", "properties": {"item": {"type": "string", "description": "Item code or name (partial match works)"}}, "required": ["item"]},
	},
	{
		"name": "get_stock_by_item",
		"description": "Item-wise stock list: total stock of every item (or of one warehouse), optionally filtered by a search word. Sorted by highest or lowest stock.",
		"parameters": {"type": "object", "properties": {
			"search": {"type": "string", "description": "Optional word in item code or name"},
			"warehouse": {"type": "string", "description": "Optional warehouse (partial match works)"},
			"order": {"type": "string", "description": "'high' (default) or 'low' stock first"},
			"count": {"type": "integer", "description": "Default 25, max 100"}}},
	},
	{
		"name": "get_low_stock_items",
		"description": "Stock items whose total stock is at or below a threshold (default 10 units), lowest first.",
		"parameters": {"type": "object", "properties": {"threshold": {"type": "number", "description": "Default 10"}, "count": {"type": "integer", "description": "Default 20, max 50"}}},
	},
	{
		"name": "get_item_pending_orders",
		"description": "Pending customer orders for an item: submitted Sales Orders where the item is not fully delivered yet, with pending qty, customer and delivery date.",
		"parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
	},
	{
		"name": "get_item_pending_purchase",
		"description": "Incoming stock of an item: submitted Purchase Orders where the item is not fully received yet.",
		"parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
	},
	{
		"name": "get_pending_sales_orders",
		"description": "Sales Orders still pending delivery (all items), earliest delivery date first. Customer is optional.",
		"parameters": {"type": "object", "properties": {"customer": {"type": "string", "description": "Optional"}, "count": {"type": "integer", "description": "Default 15, max 30"}}},
	},
	{
		"name": "get_pending_purchase_orders",
		"description": "Purchase Orders still pending receipt (all items). Supplier is optional.",
		"parameters": {"type": "object", "properties": {"supplier": {"type": "string", "description": "Optional"}, "count": {"type": "integer", "description": "Default 15, max 30"}}},
	},
	{
		"name": "get_item_price",
		"description": "Standard rate and price list rates (selling and buying) of an item.",
		"parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
	},
	{
		"name": "get_item_sales_summary",
		"description": "How much of an item was sold in a date range: quantity, amount, number of bills and top customers.",
		"parameters": {"type": "object", "properties": {"item": {"type": "string"}, "from_date": {"type": "string", "description": "YYYY-MM-DD"}, "to_date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["item", "from_date", "to_date"]},
	},
	{
		"name": "get_top_selling_items",
		"description": "Best selling items in a date range, ranked by sales amount.",
		"parameters": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}, "count": {"type": "integer", "description": "Default 10, max 20"}}, "required": ["from_date", "to_date"]},
	},
	{
		"name": "get_last_purchase_invoices",
		"description": "Latest Purchase Invoices (supplier bills) of a supplier, with items.",
		"parameters": {"type": "object", "properties": {"supplier": {"type": "string"}, "count": {"type": "integer", "description": "Default 1, max 5"}}, "required": ["supplier"]},
	},
	{
		"name": "get_supplier_outstanding",
		"description": "How much we still owe a supplier, with the list of unpaid purchase invoices.",
		"parameters": {"type": "object", "properties": {"supplier": {"type": "string"}}, "required": ["supplier"]},
	},
	{
		"name": "get_overdue_invoices",
		"description": "Sales Invoices past their due date and still unpaid: who owes us money, how much and for how many days.",
		"parameters": {"type": "object", "properties": {"count": {"type": "integer", "description": "How many to list, default 10, max 30"}}},
	},
	{
		"name": "get_receivables_summary",
		"description": "Total money to receive from customers and the top 5 customers who owe the most.",
		"parameters": {"type": "object", "properties": {}},
	},
	{
		"name": "get_payables_summary",
		"description": "Total money we owe suppliers and the top 5 suppliers we owe the most.",
		"parameters": {"type": "object", "properties": {}},
	},
]

BIZ_PERM = {
	"get_item_stock": "Bin",
	"get_low_stock_items": "Bin",
	"get_stock_by_item": "Bin",
	"get_item_pending_orders": "Sales Order",
	"get_item_pending_purchase": "Purchase Order",
	"get_pending_sales_orders": "Sales Order",
	"get_pending_purchase_orders": "Purchase Order",
	"get_item_price": "Item Price",
	"get_item_sales_summary": "Sales Invoice",
	"get_top_selling_items": "Sales Invoice",
	"get_last_purchase_invoices": "Purchase Invoice",
	"get_supplier_outstanding": "Purchase Invoice",
	"get_overdue_invoices": "Sales Invoice",
	"get_receivables_summary": "Sales Invoice",
	"get_payables_summary": "Purchase Invoice",
}


# ---------------- Stock ----------------

def get_item_stock(item):
	it, err = _resolve_item(item)
	if err:
		return err
	bins = frappe.get_list(
		"Bin", filters={"item_code": it.name},
		fields=["warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"], limit=100,
	)
	tot = lambda k: sum((b.get(k) or 0) for b in bins)
	return {
		"item": it.item_name, "item_code": it.name, "uom": it.stock_uom,
		"total_actual_qty": tot("actual_qty"), "total_reserved_qty": tot("reserved_qty"),
		"total_incoming_qty": tot("ordered_qty"), "total_projected_qty": tot("projected_qty"),
		"by_warehouse": bins,
		"note": "" if bins else "No stock records exist for this item (stock is 0)",
	}


def get_stock_by_item(search=None, warehouse=None, order="high", count=25):
	count = _n(count, 25, 1, 100)
	filters = {}
	if warehouse:
		filters["warehouse"] = ["like", f"%{warehouse}%"]
	if search:
		codes = [
			r.name for r in frappe.get_list(
				"Item", filters={"disabled": 0},
				or_filters={"name": ["like", f"%{search}%"], "item_name": ["like", f"%{search}%"]},
				fields=["name"], limit=500,
			)
		]
		if not codes:
			return {"error": f"No item found matching '{search}'"}
		filters["item_code"] = ["in", codes]
	bins = frappe.get_list("Bin", filters=filters, fields=["item_code", "actual_qty"], limit=20000)
	totals = {}
	for b in bins:
		totals[b.item_code] = totals.get(b.item_code, 0) + (b.actual_qty or 0)
	ranked = sorted(totals.items(), key=lambda x: x[1], reverse=(str(order).lower() != "low"))
	top = ranked[:count]
	meta = {
		r.name: r for r in frappe.get_list(
			"Item", filters={"name": ["in", [c for c, _ in top]]},
			fields=["name", "item_name", "stock_uom"], limit=count,
		)
	}
	return {
		"items_with_stock_records": len(totals), "showing": len(top),
		"items": [
			{"item": meta[c].item_name if c in meta else c, "item_code": c, "stock": q,
			 "uom": meta[c].stock_uom if c in meta else None}
			for c, q in top
		],
	}


def get_low_stock_items(threshold=10, count=20):
	threshold = float(threshold if threshold is not None else 10)
	count = _n(count, 20, 1, 50)
	bins = frappe.get_list("Bin", fields=["item_code", "actual_qty"], limit=20000)
	totals = {}
	for b in bins:
		totals[b.item_code] = totals.get(b.item_code, 0) + (b.actual_qty or 0)
	low = sorted(((c, q) for c, q in totals.items() if q <= threshold), key=lambda x: x[1])[:200]
	if not low:
		return {"threshold": threshold, "items": [], "note": "No items at or below the threshold"}
	meta = {
		r.name: r for r in frappe.get_list(
			"Item", filters={"name": ["in", [c for c, _ in low]], "disabled": 0, "is_stock_item": 1},
			fields=["name", "item_name", "stock_uom"], limit=200,
		)
	}
	out = [
		{"item": meta[c].item_name, "item_code": c, "stock": q, "uom": meta[c].stock_uom}
		for c, q in low if c in meta
	][:count]
	return {"threshold": threshold, "items": out}


# ---------------- Pending orders ----------------

def get_item_pending_orders(item):
	it, err = _resolve_item(item)
	if err:
		return err
	rows = frappe.db.sql(
		"""
		SELECT so.name AS sales_order, so.customer, so.transaction_date, so.delivery_date, so.status,
		       soi.qty, soi.delivered_qty, (soi.qty - soi.delivered_qty) AS pending_qty
		FROM `tabSales Order Item` soi JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.status NOT IN %(skip)s
		  AND soi.item_code = %(i)s AND soi.qty > soi.delivered_qty
		ORDER BY so.delivery_date, so.transaction_date LIMIT 50
		""",
		{"i": it.name, "skip": NOT_PENDING_SO}, as_dict=True,
	)
	return {
		"item": it.item_name, "uom": it.stock_uom, "pending_orders": len(rows),
		"total_pending_qty": sum(r.pending_qty for r in rows), "orders": rows,
	}


def get_item_pending_purchase(item):
	it, err = _resolve_item(item)
	if err:
		return err
	rows = frappe.db.sql(
		"""
		SELECT po.name AS purchase_order, po.supplier, po.transaction_date, poi.schedule_date, po.status,
		       poi.qty, poi.received_qty, (poi.qty - poi.received_qty) AS pending_qty
		FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE po.docstatus = 1 AND po.status NOT IN %(skip)s
		  AND poi.item_code = %(i)s AND poi.qty > poi.received_qty
		ORDER BY poi.schedule_date, po.transaction_date LIMIT 50
		""",
		{"i": it.name, "skip": NOT_PENDING_PO}, as_dict=True,
	)
	return {
		"item": it.item_name, "uom": it.stock_uom, "pending_purchase_orders": len(rows),
		"total_incoming_qty": sum(r.pending_qty for r in rows), "orders": rows,
	}


def get_pending_sales_orders(customer=None, count=15):
	filters = {"docstatus": 1, "status": ["not in", list(NOT_PENDING_SO)], "per_delivered": ["<", 100]}
	if customer:
		cust, err = _resolve_party("Customer", "customer_name", customer)
		if err:
			return err
		filters["customer"] = cust
	rows = frappe.get_list(
		"Sales Order", filters=filters,
		fields=["name", "customer", "transaction_date", "delivery_date", "grand_total", "per_delivered", "per_billed", "status", "currency"],
		order_by="delivery_date asc, transaction_date asc", limit=_n(count, 15, 1, 30),
	)
	return {"count": len(rows), "orders": rows}


def get_pending_purchase_orders(supplier=None, count=15):
	filters = {"docstatus": 1, "status": ["not in", list(NOT_PENDING_PO)], "per_received": ["<", 100]}
	if supplier:
		sup, err = _resolve_party("Supplier", "supplier_name", supplier)
		if err:
			return err
		filters["supplier"] = sup
	rows = frappe.get_list(
		"Purchase Order", filters=filters,
		fields=["name", "supplier", "transaction_date", "schedule_date", "grand_total", "per_received", "per_billed", "status", "currency"],
		order_by="schedule_date asc, transaction_date asc", limit=_n(count, 15, 1, 30),
	)
	return {"count": len(rows), "orders": rows}


# ---------------- Item price & sales analysis ----------------

def get_item_price(item):
	it, err = _resolve_item(item)
	if err:
		return err
	std = frappe.get_list("Item", filters={"name": it.name}, fields=["standard_rate", "valuation_rate"], limit=1)
	prices = frappe.get_list(
		"Item Price", filters={"item_code": it.name},
		fields=["price_list", "price_list_rate", "currency", "selling", "buying", "valid_from"], limit=20,
	)
	return {"item": it.item_name, "standard_rate": std[0].standard_rate if std else None, "price_lists": prices}


def get_item_sales_summary(item, from_date, to_date):
	it, err = _resolve_item(item)
	if err:
		return err
	args = {"i": it.name, "f": from_date, "t": to_date}
	tot = frappe.db.sql(
		"""SELECT COUNT(DISTINCT si.name) AS bills, COALESCE(SUM(sii.qty),0) AS qty, COALESCE(SUM(sii.amount),0) AS amount
		   FROM `tabSales Invoice Item` sii JOIN `tabSales Invoice` si ON si.name = sii.parent
		   WHERE si.docstatus = 1 AND sii.item_code = %(i)s AND si.posting_date BETWEEN %(f)s AND %(t)s""",
		args, as_dict=True,
	)[0]
	top = frappe.db.sql(
		"""SELECT si.customer, SUM(sii.qty) AS qty, SUM(sii.amount) AS amount
		   FROM `tabSales Invoice Item` sii JOIN `tabSales Invoice` si ON si.name = sii.parent
		   WHERE si.docstatus = 1 AND sii.item_code = %(i)s AND si.posting_date BETWEEN %(f)s AND %(t)s
		   GROUP BY si.customer ORDER BY amount DESC LIMIT 5""",
		args, as_dict=True,
	)
	return {"item": it.item_name, "uom": it.stock_uom, "from": from_date, "to": to_date, **tot, "top_customers": top}


def get_top_selling_items(from_date, to_date, count=10):
	rows = frappe.db.sql(
		"""SELECT sii.item_code, sii.item_name, SUM(sii.qty) AS qty, SUM(sii.amount) AS amount
		   FROM `tabSales Invoice Item` sii JOIN `tabSales Invoice` si ON si.name = sii.parent
		   WHERE si.docstatus = 1 AND si.posting_date BETWEEN %(f)s AND %(t)s
		   GROUP BY sii.item_code, sii.item_name ORDER BY amount DESC LIMIT %(l)s""",
		{"f": from_date, "t": to_date, "l": _n(count, 10, 1, 20)}, as_dict=True,
	)
	return {"from": from_date, "to": to_date, "items": rows}


# ---------------- Purchase ----------------

def get_last_purchase_invoices(supplier, count=1):
	sup, err = _resolve_party("Supplier", "supplier_name", supplier)
	if err:
		return err
	docs = frappe.get_list(
		"Purchase Invoice", filters={"supplier": sup, "docstatus": 1},
		fields=["name", "posting_date", "bill_no", "grand_total", "outstanding_amount", "status", "currency"],
		order_by="posting_date desc, creation desc", limit=_n(count, 1, 1, 5),
	)
	if not docs:
		return {"error": f"No submitted purchase invoice found for {sup}"}
	for d in docs:
		d["items"] = frappe.get_all(
			"Purchase Invoice Item", filters={"parent": d.name},
			fields=["item_code", "item_name", "qty", "rate", "amount"], order_by="idx",
		)
	return {"supplier": sup, "invoices": docs}


def get_supplier_outstanding(supplier):
	sup, err = _resolve_party("Supplier", "supplier_name", supplier)
	if err:
		return err
	rows = frappe.get_list(
		"Purchase Invoice",
		filters={"supplier": sup, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount"],
		order_by="posting_date asc", limit=50,
	)
	return {"supplier": sup, "total_outstanding": sum(r.outstanding_amount for r in rows), "unpaid_bills": rows}


# ---------------- Accounts ----------------

def get_overdue_invoices(count=10):
	today = nowdate()
	rows = frappe.get_list(
		"Sales Invoice",
		filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", today]},
		fields=["name", "customer", "posting_date", "due_date", "grand_total", "outstanding_amount", "currency"],
		order_by="due_date asc", limit=500,
	)
	for r in rows:
		r["days_overdue"] = date_diff(today, r.due_date)
	return {
		"total_overdue_invoices": len(rows),
		"total_overdue_amount": sum(r.outstanding_amount for r in rows),
		"oldest_first": rows[: _n(count, 10, 1, 30)],
	}


def _top_parties(doctype, party_field):
	rows = frappe.get_list(
		doctype, filters={"docstatus": 1, "outstanding_amount": [">", 0]},
		fields=[party_field, "outstanding_amount"], limit=5000,
	)
	by = {}
	for r in rows:
		by[r[party_field]] = by.get(r[party_field], 0) + r.outstanding_amount
	top = sorted(by.items(), key=lambda x: -x[1])[:5]
	return {
		"total_outstanding": sum(by.values()), "parties_with_outstanding": len(by),
		"top_5": [{"name": k, "outstanding": v} for k, v in top],
	}


def get_receivables_summary():
	return _top_parties("Sales Invoice", "customer")


def get_payables_summary():
	return _top_parties("Purchase Invoice", "supplier")


BIZ_FUNCS = {
	"get_item_stock": get_item_stock,
	"get_low_stock_items": get_low_stock_items,
	"get_stock_by_item": get_stock_by_item,
	"get_item_pending_orders": get_item_pending_orders,
	"get_item_pending_purchase": get_item_pending_purchase,
	"get_pending_sales_orders": get_pending_sales_orders,
	"get_pending_purchase_orders": get_pending_purchase_orders,
	"get_item_price": get_item_price,
	"get_item_sales_summary": get_item_sales_summary,
	"get_top_selling_items": get_top_selling_items,
	"get_last_purchase_invoices": get_last_purchase_invoices,
	"get_supplier_outstanding": get_supplier_outstanding,
	"get_overdue_invoices": get_overdue_invoices,
	"get_receivables_summary": get_receivables_summary,
	"get_payables_summary": get_payables_summary,
}
