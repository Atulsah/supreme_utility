# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from supreme_utility.messages import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.from_date and filters.to_date and filters.to_date < filters.from_date:
		frappe.throw(_("To Date cannot be before From Date."))

	data = get_data(filters)
	return get_columns(), data, None, None, get_summary(data)


def get_columns():
	cur = "Currency"
	return [
		# Plan
		{"fieldname": "name", "label": _("PJP No"), "fieldtype": "Link", "options": "PJP Plan", "width": 150},
		{"fieldname": "salesperson", "label": _("Salesperson"), "fieldtype": "Link", "options": "Sales Person", "width": 140},
		{"fieldname": "claim_mode", "label": _("Claim Mode"), "fieldtype": "Data", "width": 130},
		{"fieldname": "plan_type", "label": _("Plan Type"), "fieldtype": "Data", "width": 80},
		{"fieldname": "from_date", "label": _("From Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "to_date", "label": _("To Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "purpose", "label": _("Purpose"), "fieldtype": "Data", "width": 180},
		{"fieldname": "territory", "label": _("Territory"), "fieldtype": "Link", "options": "Territory", "width": 110},
		{"fieldname": "campaign", "label": _("Campaign"), "fieldtype": "Link", "options": "Campaign", "width": 110},
		# Estimate
		{"fieldname": "visits_planned", "label": _("Visits Planned"), "fieldtype": "Int", "width": 100},
		{"fieldname": "total_estimated_cost", "label": _("Estimated Cost"), "fieldtype": cur, "width": 120},
		{"fieldname": "total_expected_business", "label": _("Expected Business"), "fieldtype": cur, "width": 130},
		# Actual
		{"fieldname": "visits_done", "label": _("Visits Done"), "fieldtype": "Int", "width": 90},
		{"fieldname": "unplanned_visits", "label": _("Unplanned Visits"), "fieldtype": "Int", "width": 110},
		{"fieldname": "total_actual_cost", "label": _("Actual Cost"), "fieldtype": cur, "width": 120},
		{"fieldname": "total_order_value", "label": _("Order Value"), "fieldtype": cur, "width": 120},
		{"fieldname": "total_unplanned_business", "label": _("Unplanned Order Value"), "fieldtype": cur, "width": 140},
		{"fieldname": "employee_advance", "label": _("Employee Advance"), "fieldtype": "Link", "options": "Employee Advance", "width": 150},
		{"fieldname": "advance_paid", "label": _("Advance Paid"), "fieldtype": cur, "width": 115},
		{"fieldname": "advance_returned", "label": _("Advance Returned"), "fieldtype": cur, "width": 125},
		{"fieldname": "expense_claim", "label": _("Expense Claim"), "fieldtype": "Link", "options": "Expense Claim", "width": 150},
		{"fieldname": "claim_payable", "label": _("Claim Payable"), "fieldtype": cur, "width": 115},
		{"fieldname": "claim_paid", "label": _("Claim Paid"), "fieldtype": cur, "width": 105},
		# Variance
		{"fieldname": "cost_variance", "label": _("Cost Variance"), "fieldtype": cur, "width": 120},
		{"fieldname": "variance_percent", "label": _("Variance %"), "fieldtype": "Percent", "width": 95},
		{"fieldname": "roi_percent", "label": _("ROI %"), "fieldtype": "Float", "precision": 2, "width": 90},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
	]


def get_data(filters):
	conditions = {}
	if filters.from_date and filters.to_date:
		conditions["from_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		conditions["from_date"] = [">=", filters.from_date]
	elif filters.to_date:
		conditions["from_date"] = ["<=", filters.to_date]

	for key in ("company", "salesperson", "territory", "status", "campaign", "claim_mode"):
		if filters.get(key):
			conditions[key] = filters[key]

	# get_list applies the PJP Plan permission query, so a Sales User only sees their own rows
	plans = frappe.get_list(
		"PJP Plan",
		filters=conditions,
		fields=[
			"name", "salesperson", "claim_mode", "plan_type", "from_date", "to_date", "territory",
			"campaign", "total_estimated_cost", "total_expected_business", "total_actual_cost",
			"total_order_value", "total_unplanned_business", "employee_advance", "expense_claim", "cost_variance",
			"roi_percent", "status",
		],
		order_by="from_date desc, name desc",
		limit_page_length=0,
	)
	if not plans:
		return []

	visits = frappe.get_all(
		"PJP Visit",
		filters={"parenttype": "PJP Plan", "parent": ["in", [p.name for p in plans]]},
		fields=["parent", "actual_visit_date", "is_unplanned", "purpose"],
		order_by="parent, idx",
	)
	planned, done, unplanned, purposes = {}, {}, {}, {}
	for v in visits:
		if v.purpose:
			lst = purposes.setdefault(v.parent, [])
			if v.purpose not in lst:
				lst.append(v.purpose)
		if v.is_unplanned:
			unplanned[v.parent] = unplanned.get(v.parent, 0) + 1
		else:
			planned[v.parent] = planned.get(v.parent, 0) + 1
		if v.actual_visit_date:
			done[v.parent] = done.get(v.parent, 0) + 1

	# Money status of the linked advances and claims (read live from those documents)
	adv_names = [p.employee_advance for p in plans if p.employee_advance]
	clm_names = [p.expense_claim for p in plans if p.expense_claim]
	advances = {
		a.name: a
		for a in (
			frappe.get_all(
				"Employee Advance", filters={"name": ["in", adv_names]}, fields=["name", "paid_amount", "return_amount"]
			)
			if adv_names
			else []
		)
	}
	claims = {
		c.name: c
		for c in (
			frappe.get_all(
				"Expense Claim",
				filters={"name": ["in", clm_names]},
				fields=["name", "grand_total", "total_amount_reimbursed"],
			)
			if clm_names
			else []
		)
	}

	for p in plans:
		adv = advances.get(p.employee_advance)
		p.advance_paid = flt(adv.paid_amount) if adv else None
		p.advance_returned = flt(adv.return_amount) if adv else None
		clm = claims.get(p.expense_claim)
		p.claim_payable = flt(clm.grand_total) if clm else None
		p.claim_paid = flt(clm.total_amount_reimbursed) if clm else None

		# Ad-Hoc Visit visits are all actual: nothing was planned
		claim_only = p.claim_mode == "Ad-Hoc Visit"
		p.visits_planned = 0 if claim_only else planned.get(p.name, 0)
		p.visits_done = done.get(p.name, 0)
		# Purpose is per visit now: show the distinct purposes of the PJP
		p.purpose = ", ".join(purposes.get(p.name, []))
		p.unplanned_visits = 0 if claim_only else unplanned.get(p.name, 0)
		p.variance_percent = (
			flt(p.cost_variance) / flt(p.total_estimated_cost) * 100 if flt(p.total_estimated_cost) else None
		)
	return plans


def get_summary(data):
	rois = [flt(d.roi_percent) for d in data if d.roi_percent is not None]
	return [
		{"value": sum(flt(d.total_estimated_cost) for d in data), "label": _("Total Estimated Cost"), "datatype": "Currency"},
		{"value": sum(flt(d.total_actual_cost) for d in data), "label": _("Total Actual Cost"), "datatype": "Currency"},
		{"value": sum(flt(d.total_order_value) for d in data), "label": _("Total Order Value"), "datatype": "Currency"},
		{"value": sum(flt(d.total_unplanned_business) for d in data), "label": _("Unplanned Order Value"), "datatype": "Currency"},
		{"value": flt(sum(rois) / len(rois), 2) if rois else 0, "label": _("Average ROI %"), "datatype": "Float"},
	]
