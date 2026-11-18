"""ERPNext data tools. All read-only and user-permission checked.
Format is provider-neutral; each AI adapter converts it to its own format."""
import json

import frappe

from supreme_utility.ai_assistant.business_tools import BIZ_FUNCS, BIZ_PERM, BIZ_TOOLS
from supreme_utility.ai_assistant.explore_tools import (
	EXPLORE_FUNCS, EXPLORE_PERM, EXPLORE_TOOLS, form_url,
)
from supreme_utility.ai_assistant.hr_tools import HR_FUNCS, HR_PERM, HR_TOOLS

TOOLS = [
	{
		"name": "get_last_bill",
		"description": "The latest submitted Sales Invoice (bill) of a customer, with items.",
		"parameters": {
			"type": "object",
			"properties": {"customer": {"type": "string", "description": "Customer name (partial match works)"}},
			"required": ["customer"],
		},
	},
	{
		"name": "get_item_price_history",
		"description": "The last N sale rates of an item for a customer (latest first).",
		"parameters": {
			"type": "object",
			"properties": {
				"customer": {"type": "string"},
				"item": {"type": "string", "description": "Item code or item name (partial match works)"},
				"limit": {"type": "integer", "description": "Default 5"},
			},
			"required": ["customer", "item"],
		},
	},
	{
		"name": "get_last_sales_orders",
		"description": "The latest Sales Orders of a customer (excluding cancelled), with items. Includes draft and submitted.",
		"parameters": {
			"type": "object",
			"properties": {
				"customer": {"type": "string"},
				"count": {"type": "integer", "description": "How many orders, default 1, max 5"},
			},
			"required": ["customer"],
		},
	},
	{
		"name": "get_last_delivery_notes",
		"description": "The latest Delivery Notes of a customer, with items.",
		"parameters": {
			"type": "object",
			"properties": {
				"customer": {"type": "string"},
				"count": {"type": "integer", "description": "Default 1, max 5"},
			},
			"required": ["customer"],
		},
	},
	{
		"name": "get_last_payments",
		"description": "The latest payments received from a customer (Payment Entry, Receive).",
		"parameters": {
			"type": "object",
			"properties": {
				"customer": {"type": "string"},
				"count": {"type": "integer", "description": "Default 1, max 5"},
			},
			"required": ["customer"],
		},
	},
	{
		"name": "get_customer_outstanding",
		"description": "Total outstanding amount of a customer and the list of unpaid bills.",
		"parameters": {
			"type": "object",
			"properties": {"customer": {"type": "string"}},
			"required": ["customer"],
		},
	},
	{
		"name": "get_sales_summary",
		"description": "Sales summary for a date range: total, bill count, outstanding, top customers.",
		"parameters": {
			"type": "object",
			"properties": {
				"from_date": {"type": "string", "description": "YYYY-MM-DD"},
				"to_date": {"type": "string", "description": "YYYY-MM-DD"},
			},
			"required": ["from_date", "to_date"],
		},
	},
]


def _resolve_customer(text):
	rows = frappe.get_list(
		"Customer",
		or_filters={"name": ["like", f"%{text}%"], "customer_name": ["like", f"%{text}%"]},
		fields=["name", "customer_name"],
		limit=5,
	)
	if not rows:
		return None, {"error": f"No customer found matching '{text}'"}
	if len(rows) > 1:
		return None, {
			"error": "Multiple customers matched, ask the user to confirm which one",
			"matches": [r.customer_name for r in rows],
		}
	return rows[0].name, None


def get_last_bill(customer):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	inv = frappe.get_list(
		"Sales Invoice",
		filters={"customer": cust, "docstatus": 1},
		fields=["name", "posting_date", "grand_total", "outstanding_amount", "status", "currency"],
		order_by="posting_date desc, creation desc",
		limit=1,
	)
	if not inv:
		return {"error": f"No submitted bill found for {cust}"}
	inv = inv[0]
	inv["customer"] = cust
	inv["items"] = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": inv.name},
		fields=["item_name", "qty", "rate", "amount"],
		order_by="idx",
	)
	return inv


def get_item_price_history(customer, item, limit=5):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	limit = max(1, min(int(limit or 5), 20))
	rows = frappe.db.sql(
		"""
		SELECT si.name AS invoice, si.posting_date, sii.item_code, sii.item_name,
		       sii.qty, sii.rate
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1 AND si.customer = %(c)s
		  AND (sii.item_code LIKE %(i)s OR sii.item_name LIKE %(i)s)
		ORDER BY si.posting_date DESC, si.creation DESC
		LIMIT %(l)s
		""",
		{"c": cust, "i": f"%{item}%", "l": limit},
		as_dict=True,
	)
	if not rows:
		return {"error": f"No sale of '{item}' found for {cust}"}
	return {"customer": cust, "history": rows}


def get_sales_summary(from_date, to_date):
	args = {"f": from_date, "t": to_date}
	total = frappe.db.sql(
		"""SELECT COUNT(*) AS bills, COALESCE(SUM(grand_total),0) AS total,
		          COALESCE(SUM(outstanding_amount),0) AS outstanding
		   FROM `tabSales Invoice`
		   WHERE docstatus=1 AND posting_date BETWEEN %(f)s AND %(t)s""",
		args,
		as_dict=True,
	)[0]
	top = frappe.db.sql(
		"""SELECT customer, SUM(grand_total) AS total
		   FROM `tabSales Invoice`
		   WHERE docstatus=1 AND posting_date BETWEEN %(f)s AND %(t)s
		   GROUP BY customer ORDER BY total DESC LIMIT 5""",
		args,
		as_dict=True,
	)
	return {"from": from_date, "to": to_date, **total, "top_customers": top}


def _last_docs(doctype, date_field, fields, cust, count, item_doctype=None, item_fields=None):
	count = max(1, min(int(count or 1), 5))
	docs = frappe.get_list(
		doctype,
		filters={"customer": cust, "docstatus": ["<", 2]},
		fields=["name", date_field, "docstatus", "status"] + fields,
		order_by=f"{date_field} desc, creation desc",
		limit=count,
	)
	if item_doctype:
		for d in docs:
			d["items"] = frappe.get_all(
				item_doctype, filters={"parent": d.name}, fields=item_fields, order_by="idx"
			)
	return docs


def get_last_sales_orders(customer, count=1):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	docs = _last_docs(
		"Sales Order", "transaction_date",
		["delivery_date", "grand_total", "currency", "per_delivered", "per_billed"],
		cust, count, "Sales Order Item", ["item_name", "qty", "rate", "amount"],
	)
	if not docs:
		return {"error": f"No Sales Order found for {cust}"}
	return {"customer": cust, "orders": docs}


def get_last_delivery_notes(customer, count=1):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	docs = _last_docs(
		"Delivery Note", "posting_date", ["grand_total", "currency"],
		cust, count, "Delivery Note Item", ["item_name", "qty", "rate", "amount"],
	)
	if not docs:
		return {"error": f"No Delivery Note found for {cust}"}
	return {"customer": cust, "delivery_notes": docs}


def get_last_payments(customer, count=1):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	count = max(1, min(int(count or 1), 5))
	docs = frappe.get_list(
		"Payment Entry",
		filters={"party_type": "Customer", "party": cust, "docstatus": 1, "payment_type": "Receive"},
		fields=["name", "posting_date", "paid_amount", "mode_of_payment", "reference_no", "paid_to_account_currency"],
		order_by="posting_date desc, creation desc",
		limit=count,
	)
	if not docs:
		return {"error": f"No payment found for {cust}"}
	return {"customer": cust, "payments": docs}


def get_customer_outstanding(customer):
	cust, err = _resolve_customer(customer)
	if err:
		return err
	rows = frappe.get_list(
		"Sales Invoice",
		filters={"customer": cust, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount"],
		order_by="posting_date asc",
		limit=50,
	)
	return {
		"customer": cust,
		"total_outstanding": sum(r.outstanding_amount for r in rows),
		"unpaid_bills": rows,
	}


_FUNCS = {
	"get_last_sales_orders": get_last_sales_orders,
	"get_last_delivery_notes": get_last_delivery_notes,
	"get_last_payments": get_last_payments,
	"get_customer_outstanding": get_customer_outstanding,
	"get_last_bill": get_last_bill,
	"get_item_price_history": get_item_price_history,
	"get_sales_summary": get_sales_summary,
}


_PERM = {
	"get_last_sales_orders": "Sales Order",
	"get_last_delivery_notes": "Delivery Note",
	"get_last_payments": "Payment Entry",
}
_PERM.update(HR_PERM)
_PERM.update(BIZ_PERM)
_PERM.update(EXPLORE_PERM)
_FUNCS.update(HR_FUNCS)
_FUNCS.update(BIZ_FUNCS)
_FUNCS.update(EXPLORE_FUNCS)
# 360-degree tools first, so the AI prefers them
TOOLS[:0] = EXPLORE_TOOLS
TOOLS.extend(BIZ_TOOLS)
TOOLS.extend(HR_TOOLS)

# ---------------- Clickable links ----------------
# Which doctype the "name" of a returned row belongs to, per tool
_ROW_DOCTYPE = {
	"get_last_bill": "Sales Invoice",
	"get_customer_outstanding": "Sales Invoice",
	"get_overdue_invoices": "Sales Invoice",
	"get_last_sales_orders": "Sales Order",
	"get_pending_sales_orders": "Sales Order",
	"get_last_delivery_notes": "Delivery Note",
	"get_last_payments": "Payment Entry",
	"get_pending_purchase_orders": "Purchase Order",
	"get_last_purchase_invoices": "Purchase Invoice",
	"get_supplier_outstanding": "Purchase Invoice",
	"get_leave_applications": "Leave Application",
	"get_last_salary_slips": "Salary Slip",
	"get_expense_claims": "Expense Claim",
}
# Fields whose value is the ID of a document
_KEY_DOCTYPE = {
	"invoice": "Sales Invoice", "sales_invoice": "Sales Invoice", "sales_order": "Sales Order",
	"purchase_order": "Purchase Order", "purchase_invoice": "Purchase Invoice",
	"delivery_note": "Delivery Note", "customer": "Customer", "supplier": "Supplier",
	"item_code": "Item", "employee": "Employee",
}
_NAME_FIELDS = ("customer_name", "supplier_name", "party_name", "employee_name")


def _collect_links(obj, row_dt, out):
	"""Collect {visible text: url} for every document ID found in a tool result."""
	if isinstance(obj, list):
		for x in obj:
			_collect_links(x, row_dt, out)
		return
	if not isinstance(obj, dict):
		return
	dt = obj.get("doctype") or (row_dt if "name" in obj else None)
	if dt and obj.get("name"):
		url = form_url(dt, obj["name"])
		out.setdefault(str(obj["name"]), url)
		# also link the readable name of a party / employee row
		if dt in ("Customer", "Supplier", "Employee"):
			for f in _NAME_FIELDS:
				if obj.get(f):
					out.setdefault(str(obj[f]), url)
	for k, v in obj.items():
		if k in _KEY_DOCTYPE and isinstance(v, str) and v:
			out.setdefault(v, form_url(_KEY_DOCTYPE[k], v))
			if k == "item_code" and obj.get("item_name"):
				out.setdefault(str(obj["item_name"]), form_url("Item", v))
			if k == "customer" and obj.get("customer_name"):
				out.setdefault(str(obj["customer_name"]), form_url("Customer", v))
			if k == "supplier" and obj.get("supplier_name"):
				out.setdefault(str(obj["supplier_name"]), form_url("Supplier", v))
		elif k == "party" and isinstance(v, str) and obj.get("party_type") in ("Customer", "Supplier"):
			out.setdefault(v, form_url(obj["party_type"], v))
		elif isinstance(v, (list, dict)) and k != "matches":
			_collect_links(v, None if k == "items" else row_dt, out)


def run_tool(name, args):
	"""Runs a tool call from the AI and always returns a JSON-safe dict."""
	try:
		if name not in _FUNCS:
			return {"error": f"Unknown tool {name}"}
		if name in HR_FUNCS and "hrms" not in frappe.get_installed_apps():
			return {"error": "The HRMS app is not installed on this site"}
		perm = _PERM.get(name, "Sales Invoice")
		if perm:
			frappe.has_permission(perm, "read", throw=True)
		out = _FUNCS[name](**(args or {}))
	except frappe.PermissionError:
		out = {"error": "You do not have permission to access this data"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "AI Assistant tool error")
		out = {"error": str(e)}
	out = json.loads(json.dumps(out, default=str))
	links = getattr(frappe.local, "ai_links", None)
	if links is not None and isinstance(out, dict) and not out.get("error"):
		_collect_links(out, _ROW_DOCTYPE.get(name), links)
	return out
