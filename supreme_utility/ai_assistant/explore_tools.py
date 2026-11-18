"""360-degree tools: find any transaction by any angle (date / party / item / status / amount)
and analyse totals grouped by party, item, item group, territory, day or month.
All read-only and permission checked. Every document row carries "doctype" so the
assistant can turn its ID into a clickable link."""
import json
from urllib.parse import quote

import frappe
from frappe.utils import add_days, getdate, nowdate

# doctype -> config
DOCS = {
	"Sales Invoice": {"date": "posting_date", "party": "customer", "party_dt": "Customer",
		"child": "Sales Invoice Item", "amount": "grand_total",
		"extra": ["outstanding_amount", "due_date", "status", "currency", "is_return"]},
	"Sales Order": {"date": "transaction_date", "party": "customer", "party_dt": "Customer",
		"child": "Sales Order Item", "amount": "grand_total",
		"extra": ["delivery_date", "per_delivered", "per_billed", "status", "currency"]},
	"Delivery Note": {"date": "posting_date", "party": "customer", "party_dt": "Customer",
		"child": "Delivery Note Item", "amount": "grand_total",
		"extra": ["status", "currency", "per_billed"]},
	"Quotation": {"date": "transaction_date", "party": "party_name", "party_dt": "Customer",
		"child": "Quotation Item", "amount": "grand_total",
		"extra": ["quotation_to", "valid_till", "status", "currency"]},
	"Purchase Invoice": {"date": "posting_date", "party": "supplier", "party_dt": "Supplier",
		"child": "Purchase Invoice Item", "amount": "grand_total",
		"extra": ["bill_no", "outstanding_amount", "due_date", "status", "currency"]},
	"Purchase Order": {"date": "transaction_date", "party": "supplier", "party_dt": "Supplier",
		"child": "Purchase Order Item", "amount": "grand_total",
		"extra": ["schedule_date", "per_received", "per_billed", "status", "currency"]},
	"Purchase Receipt": {"date": "posting_date", "party": "supplier", "party_dt": "Supplier",
		"child": "Purchase Receipt Item", "amount": "grand_total",
		"extra": ["status", "currency", "per_billed"]},
	"Payment Entry": {"date": "posting_date", "party": "party", "party_dt": None,
		"child": None, "amount": "paid_amount",
		"extra": ["payment_type", "party_type", "party_name", "mode_of_payment", "reference_no", "status"]},
}
PARTY_NAME = {"Customer": "customer_name", "Supplier": "supplier_name"}
SORTS = {"latest": "{d} desc, creation desc", "oldest": "{d} asc, creation asc",
	"highest": "{a} desc", "lowest": "{a} asc"}

EXPLORE_TOOLS = [
	{
		"name": "find_documents",
		"description": (
			"Universal search for transactions. Use this for ANY 'show / list / last N / details' question "
			"about Sales Invoice, Sales Order, Delivery Note, Quotation, Purchase Invoice, Purchase Order, "
			"Purchase Receipt or Payment Entry. EVERY filter is optional: with no customer it searches all "
			"customers, so never ask the user for a party unless they want one. Combine any filters: "
			"date range, party, item, status, amount range, unpaid only. Returns documents (with items) plus "
			"the total count and total amount of everything that matched."
		),
		"parameters": {"type": "object", "properties": {
			"doctype": {"type": "string", "enum": list(DOCS), "description": "Default 'Sales Invoice'"},
			"party": {"type": "string", "description": "Optional customer or supplier name (partial match works)"},
			"item": {"type": "string", "description": "Optional item code or name: only documents containing this item"},
			"from_date": {"type": "string", "description": "Optional YYYY-MM-DD"},
			"to_date": {"type": "string", "description": "Optional YYYY-MM-DD"},
			"status": {"type": "string", "description": "Optional status, e.g. Paid, Unpaid, Overdue, Draft, To Deliver and Bill"},
			"unpaid_only": {"type": "boolean", "description": "Only invoices with outstanding amount > 0"},
			"min_amount": {"type": "number"},
			"max_amount": {"type": "number"},
			"include_drafts": {"type": "boolean", "description": "Default false (submitted only). Cancelled are never shown."},
			"sort": {"type": "string", "enum": list(SORTS), "description": "Default 'latest'"},
			"count": {"type": "integer", "description": "How many documents to return, default 5, max 50"},
			"with_items": {"type": "boolean", "description": "Include item lines, default true"},
		}},
	},
	{
		"name": "analyze_transactions",
		"description": (
			"Totals grouped by any angle: customer/supplier, item, item_group, territory, day or month. "
			"Use for 'party-wise', 'item-wise', 'date-wise', 'month-wise', 'top N', 'kis item ki sale sabse "
			"zyada' type questions. Optional filters: date range, party, item. Sales doctypes by default."
		),
		"parameters": {"type": "object", "properties": {
			"group_by": {"type": "string", "enum": ["party", "item", "item_group", "territory", "day", "month"]},
			"doctype": {"type": "string", "enum": [d for d in DOCS if d != "Payment Entry"], "description": "Default 'Sales Invoice'"},
			"from_date": {"type": "string", "description": "YYYY-MM-DD, default first day of this month"},
			"to_date": {"type": "string", "description": "YYYY-MM-DD, default today"},
			"party": {"type": "string", "description": "Optional customer or supplier"},
			"item": {"type": "string", "description": "Optional item code or name"},
			"sort": {"type": "string", "enum": ["highest", "lowest", "date"], "description": "Default 'highest' ('date' for day/month)"},
			"count": {"type": "integer", "description": "Max groups, default 15, max 100"},
		}, "required": ["group_by"]},
	},
]


def form_url(doctype, name):
	return f"/app/{frappe.scrub(doctype).replace('_', '-')}/{quote(str(name), safe='')}"


def list_url(doctype, filters):
	q = "&".join(
		f"{k}={quote(v if isinstance(v, str) else json.dumps(v), safe='')}" for k, v in filters.items()
	)
	return f"/app/{frappe.scrub(doctype).replace('_', '-')}" + (f"?{q}" if q else "")


def _resolve_party(party_dt, text):
	t = (text or "").strip()
	nf = PARTY_NAME[party_dt]
	exact = frappe.get_list(party_dt, or_filters={"name": t, nf: t}, fields=["name", nf], limit=2)
	if len(exact) == 1:
		return exact[0].name, None
	rows = frappe.get_list(party_dt, or_filters={"name": ["like", f"%{t}%"], nf: ["like", f"%{t}%"]},
		fields=["name", nf], limit=8)
	if not rows:
		return None, {"error": f"No {party_dt.lower()} found matching '{t}'"}
	if len(rows) > 1:
		return None, {"error": f"Multiple {party_dt.lower()}s matched, ask the user which one",
			"matches": [r[nf] for r in rows]}
	return rows[0].name, None


def _resolve_item(text):
	t = (text or "").strip()
	exact = frappe.get_list("Item", or_filters={"name": t, "item_name": t}, fields=["name", "item_name"], limit=2)
	if len(exact) == 1:
		return exact[0], None
	rows = frappe.get_list("Item", or_filters={"name": ["like", f"%{t}%"], "item_name": ["like", f"%{t}%"]},
		fields=["name", "item_name"], limit=8)
	if not rows:
		return None, {"error": f"No item found matching '{t}'"}
	if len(rows) > 1:
		return None, {"error": "Multiple items matched, ask the user which one",
			"matches": [f"{r.item_name} ({r.name})" for r in rows]}
	return rows[0], None


def _party_for(cfg, doctype, party):
	"""Returns (filters, party_name, error)."""
	if not party:
		return {}, None, None
	if doctype == "Payment Entry":
		errs = []
		for dt in ("Customer", "Supplier"):
			p, err = _resolve_party(dt, party)
			if p:
				return {"party_type": dt, "party": p}, p, None
			errs.append(err)
		return None, None, next((e for e in errs if e.get("matches")), errs[-1])
	p, err = _resolve_party(cfg["party_dt"], party)
	if err:
		return None, None, err
	return {cfg["party"]: p}, p, None


def find_documents(doctype="Sales Invoice", party=None, item=None, from_date=None, to_date=None,
		status=None, unpaid_only=False, min_amount=None, max_amount=None, include_drafts=False,
		sort="latest", count=5, with_items=True):
	doctype = doctype if doctype in DOCS else "Sales Invoice"
	cfg = DOCS[doctype]
	frappe.has_permission(doctype, "read", throw=True)
	count = max(1, min(int(count or 5), 50))
	d, a = cfg["date"], cfg["amount"]

	filters, party_name, err = _party_for(cfg, doctype, party)
	if err:
		return err
	filters["docstatus"] = ["<", 2] if include_drafts else 1
	if from_date and to_date:
		filters[d] = ["between", [from_date, to_date]]
	elif from_date:
		filters[d] = [">=", from_date]
	elif to_date:
		filters[d] = ["<=", to_date]
	if status:
		filters["status"] = status
	if unpaid_only and "outstanding_amount" in cfg["extra"]:
		filters["outstanding_amount"] = [">", 0]
	if min_amount is not None and max_amount is not None:
		filters[a] = ["between", [min_amount, max_amount]]
	elif min_amount is not None:
		filters[a] = [">=", min_amount]
	elif max_amount is not None:
		filters[a] = ["<=", max_amount]

	link_filters = {k: v for k, v in filters.items() if k != "docstatus"}
	item_code = None
	if item:
		if not cfg["child"]:
			return {"error": f"{doctype} has no items"}
		it, err = _resolve_item(item)
		if err:
			return err
		item_code = it.name
		parents = frappe.get_all(cfg["child"], filters={"item_code": item_code, "parenttype": doctype},
			pluck="parent", distinct=True, limit=5000)
		if not parents:
			return {"doctype": doctype, "total_matching": 0, "documents": [],
				"note": f"No {doctype} contains item {it.item_name}"}
		filters["name"] = ["in", parents]

	order = SORTS.get(sort or "latest", SORTS["latest"]).format(d=d, a=a)
	party_field = cfg["party"]
	extra_fields = list(cfg["extra"])
	if cfg["party_dt"]:
		extra_fields.append(PARTY_NAME[cfg["party_dt"]] if doctype != "Quotation" else "customer_name")
	all_rows = frappe.get_list(doctype, filters=filters,
		fields=["name", d, party_field, a] + extra_fields, order_by=order, limit=1000)
	docs = all_rows[:count]

	if with_items is not False and cfg["child"] and docs:
		lines = frappe.get_all(cfg["child"], filters={"parent": ["in", [x.name for x in docs]], "parenttype": doctype},
			fields=["parent", "item_code", "item_name", "qty", "uom", "rate", "amount"], order_by="parent, idx")
		by = {}
		for ln in lines:
			by.setdefault(ln.pop("parent"), []).append(ln)
		for x in docs:
			x["items"] = by.get(x.name, [])
	for x in docs:
		x["doctype"] = doctype

	out = {
		"doctype": doctype,
		"filters_used": {k: v for k, v in {"party": party_name, "item": item_code, "from_date": from_date,
			"to_date": to_date, "status": status, "unpaid_only": unpaid_only or None}.items() if v},
		"total_matching": len(all_rows) if len(all_rows) < 1000 else "1000+",
		"total_amount": sum((r.get(a) or 0) for r in all_rows),
		"showing": len(docs),
		"documents": docs,
	}
	if "outstanding_amount" in cfg["extra"]:
		out["total_outstanding"] = sum((r.get("outstanding_amount") or 0) for r in all_rows)
	if not item:
		out["open_list_url"] = list_url(doctype, link_filters)
	return out


def analyze_transactions(group_by, doctype="Sales Invoice", from_date=None, to_date=None,
		party=None, item=None, sort=None, count=15):
	doctype = doctype if doctype in DOCS and doctype != "Payment Entry" else "Sales Invoice"
	cfg = DOCS[doctype]
	frappe.has_permission(doctype, "read", throw=True)
	count = max(1, min(int(count or 15), 100))
	today = getdate(nowdate())
	from_date = from_date or str(today.replace(day=1))
	to_date = to_date or str(today)
	d, pf, child = cfg["date"], cfg["party"], cfg["child"]

	cond = ["p.docstatus = 1", f"p.`{d}` BETWEEN %(f)s AND %(t)s"]
	args = {"f": from_date, "t": to_date}
	party_name = None
	if party:
		pfilt, party_name, err = _party_for(cfg, doctype, party)
		if err:
			return err
		cond.append(f"p.`{pf}` = %(p)s")
		args["p"] = party_name
	if item:
		it, err = _resolve_item(item)
		if err:
			return err
		cond.append("c.item_code = %(i)s")
		args["i"] = it.name

	keys = {
		"party": (f"p.`{pf}`", cfg["party_dt"]),
		"item": ("c.item_code", "Item"),
		"item_group": ("c.item_group", "Item Group"),
		"territory": ("p.territory", "Territory"),
		"day": (f"p.`{d}`", None),
		"month": (f"DATE_FORMAT(p.`{d}`, '%%Y-%%m')", None),
	}
	if group_by not in keys:
		return {"error": f"group_by must be one of {list(keys)}"}
	if group_by == "territory" and not frappe.get_meta(doctype).has_field("territory"):
		return {"error": f"{doctype} has no territory"}
	key, link_dt = keys[group_by]
	use_child = group_by in ("item", "item_group") or bool(item)
	if use_child:
		src = f"`tab{doctype}` p JOIN `tab{child}` c ON c.parent = p.name AND c.parenttype = %(dt)s"
		val = "SUM(c.base_net_amount) AS amount, SUM(c.stock_qty) AS qty, COUNT(DISTINCT p.name) AS documents"
		args["dt"] = doctype
	else:
		src = f"`tab{doctype}` p"
		val = "SUM(p.base_grand_total) AS amount, COUNT(p.name) AS documents"
	sort = sort or ("date" if group_by in ("day", "month") else "highest")
	order = {"highest": "amount DESC", "lowest": "amount ASC", "date": "grp ASC"}.get(sort, "amount DESC")
	extra = ", MAX(c.item_name) AS item_name" if group_by == "item" else ""
	rows = frappe.db.sql(
		f"SELECT {key} AS grp, {val}{extra} FROM {src} WHERE {' AND '.join(cond)} "
		f"GROUP BY grp ORDER BY {order} LIMIT {count}",
		args, as_dict=True,
	)
	grand = frappe.db.sql(
		f"SELECT {val} FROM {src} WHERE {' AND '.join(cond)}", args, as_dict=True)[0]

	label = {"party": pf, "item": "item_code", "item_group": "item_group", "territory": "territory",
		"day": "date", "month": "month"}[group_by]
	out_rows = []
	for r in rows:
		g = r.pop("grp")
		row = {label: str(g), **r}
		if link_dt and g:
			row["doctype"] = link_dt
			row["name"] = g
			if link_dt in PARTY_NAME:
				row["party_name"] = frappe.db.get_value(link_dt, g, PARTY_NAME[link_dt])
		# a list link for this group, showing the underlying documents
		lf = {d: ["between", [from_date, to_date]]}
		if group_by == "party":
			lf[pf] = g
		elif group_by == "territory":
			lf["territory"] = g
		elif group_by == "day":
			lf[d] = str(g)
		elif group_by == "month":
			y, m = str(g).split("-")
			start = getdate(f"{y}-{m}-01")
			end = add_days(getdate(f"{int(y) + (m == '12')}-{1 if m == '12' else int(m) + 1:02d}-01"), -1)
			lf[d] = ["between", [str(start), str(end)]]
		if party_name and group_by != "party":
			lf[pf] = party_name
		if group_by not in ("item", "item_group") and not item:
			row["documents_url"] = list_url(doctype, lf)
		out_rows.append(row)

	return {
		"doctype": doctype, "group_by": group_by, "from": from_date, "to": to_date,
		"filters_used": {k: v for k, v in {"party": party_name, "item": item}.items() if v},
		"grand_total_amount": grand.amount or 0, "total_documents": grand.documents or 0,
		"grand_total_qty": grand.get("qty"),
		"amount_basis": "net amount of item lines (company currency)" if use_child else "grand total (company currency)",
		"groups": out_rows,
	}


EXPLORE_FUNCS = {"find_documents": find_documents, "analyze_transactions": analyze_transactions}
# permission is checked per doctype inside each function
EXPLORE_PERM = {"find_documents": None, "analyze_transactions": None}
