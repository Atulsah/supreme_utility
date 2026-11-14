# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Sales Calendar: one row per Sales Person, one column per day.

Status of a day (first match wins):
  leave   (red)    approved / pending HRMS Leave Application
  tour    (green)  PJP Plan of type Tour (or an Ad-Hoc Visit) covering the day
  work    (blue)   PJP Plan of type Event covering the day
  holiday (grey)   holiday / weekly off in the employee's Holiday List
  idle    (yellow) nothing planned
"""

import frappe
from frappe.utils import add_days, cstr, date_diff, formatdate, getdate

from supreme_utility.messages import _

ALLOWED_ROLES = {
	"Sales User",
	"Sales Manager",
	"HR User",
	"HR Manager",
	"Accounts User",
	"System Manager",
}
MAX_DAYS = 62


@frappe.whitelist()
def get_schedule(from_date, to_date, sales_person_group=None):
	if frappe.session.user != "Administrator" and not (ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	start, end = getdate(from_date), getdate(to_date)
	if end < start:
		start, end = end, start
	if date_diff(end, start) + 1 > MAX_DAYS:
		frappe.throw(_("Please select at most {0} days.").format(MAX_DAYS))

	days = [add_days(start, i) for i in range(date_diff(end, start) + 1)]
	persons = get_sales_persons(sales_person_group)
	employees = [p.employee for p in persons if p.employee]

	leaves = get_leaves(employees, start, end)
	holidays = get_holidays(employees, start, end)
	plans = get_plans(persons, employees, start, end)
	visits = get_visits(plans, start, end)

	rows = []
	summary = {str(d): {"leave": 0, "tour": 0, "work": 0, "holiday": 0, "idle": 0} for d in days}
	for p in persons:
		cells = {}
		for d in days:
			cell = build_cell(p, d, leaves, holidays, plans, visits)
			cells[str(d)] = cell
			summary[str(d)][cell["s"]] += 1
		rows.append(
			{
				"name": p.name,
				"label": p.sales_person_name or p.name,
				"employee": p.employee,
				"group": p.parent_sales_person,
				"cells": cells,
			}
		)

	return {
		"from_date": str(start),
		"to_date": str(end),
		"days": [str(d) for d in days],
		"rows": rows,
		"summary": summary,
		"today": frappe.utils.nowdate(),
	}


# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------
def get_sales_persons(group=None):
	filters = {"is_group": 0, "enabled": 1}
	if group:
		bounds = frappe.db.get_value("Sales Person", group, ["lft", "rgt", "is_group"], as_dict=True)
		if bounds and bounds.is_group:
			filters["lft"] = [">", bounds.lft]
			filters["rgt"] = ["<", bounds.rgt]
		elif bounds:
			filters["name"] = group
	return frappe.get_all(
		"Sales Person",
		filters=filters,
		fields=["name", "sales_person_name", "employee", "parent_sales_person"],
		order_by="sales_person_name asc",
	)


def get_leaves(employees, start, end):
	if not employees or not frappe.db.exists("DocType", "Leave Application"):
		return {}
	rows = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", employees],
			"docstatus": ["in", [0, 1]],
			"status": ["in", ["Open", "Approved"]],
			"from_date": ["<=", end],
			"to_date": [">=", start],
		},
		fields=[
			"name", "employee", "leave_type", "from_date", "to_date", "half_day",
			"half_day_date", "total_leave_days", "status", "docstatus", "description",
		],
		order_by="from_date asc",
	)
	out = {}
	for r in rows:
		out.setdefault(r.employee, []).append(r)
	return out


def get_holidays(employees, start, end):
	"""{employee: {date_str: description}} from each employee's Holiday List (else the company default)."""
	if not employees:
		return {}
	emp_rows = frappe.get_all(
		"Employee", filters={"name": ["in", employees]}, fields=["name", "holiday_list", "company"]
	)
	company_lists = {}
	list_of = {}
	for e in emp_rows:
		hl = e.holiday_list
		if not hl and e.company:
			if e.company not in company_lists:
				company_lists[e.company] = frappe.get_cached_value("Company", e.company, "default_holiday_list")
			hl = company_lists[e.company]
		list_of[e.name] = hl

	lists = {h for h in list_of.values() if h}
	by_list = {}
	if lists:
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": ["in", list(lists)], "holiday_date": ["between", [start, end]]},
			fields=["parent", "holiday_date", "description", "weekly_off"],
		):
			by_list.setdefault(h.parent, {})[cstr(h.holiday_date)] = h

	return {emp: by_list.get(hl, {}) for emp, hl in list_of.items()}


def get_plans(persons, employees, start, end):
	"""PJP Plans overlapping the range, grouped by Sales Person name and by employee."""
	names = [p.name for p in persons]
	or_filters = []
	if names:
		or_filters.append(["salesperson", "in", names])
	if employees:
		or_filters.append(["employee", "in", employees])
	if not or_filters:
		return {}
	rows = frappe.get_all(
		"PJP Plan",
		filters={
			"docstatus": ["in", [0, 1]],
			"status": ["!=", "Cancelled"],
			"from_date": ["<=", end],
			"to_date": [">=", start],
		},
		or_filters=or_filters,
		fields=[
			"name", "salesperson", "employee", "plan_type", "claim_mode", "status", "docstatus",
			"from_date", "to_date", "territory", "total_expected_business",
		],
		order_by="from_date asc",
	)
	out = {}
	emp_to_person = {p.employee: p.name for p in persons if p.employee}
	for r in rows:
		key = r.salesperson if r.salesperson in names else emp_to_person.get(r.employee)
		if key:
			out.setdefault(key, []).append(r)
	return out


def get_visits(plans, start, end):
	"""{pjp name: {date_str: [visit rows]}} (actual date if known, else planned date)."""
	pjp_names = [p.name for rows in plans.values() for p in rows]
	if not pjp_names:
		return {}
	rows = frappe.get_all(
		"PJP Visit",
		filters={"parent": ["in", pjp_names], "parenttype": "PJP Plan"},
		fields=[
			"parent", "party_name", "organization_name", "purpose", "destination",
			"planned_date", "actual_visit_date", "visit_result",
		],
		order_by="idx asc",
	)
	out = {}
	for r in rows:
		day = r.actual_visit_date or r.planned_date
		if day:
			out.setdefault(r.parent, {}).setdefault(cstr(day), []).append(r)
	return out


# ----------------------------------------------------------------------
# One cell
# ----------------------------------------------------------------------
def build_cell(person, day, leaves, holidays, plans, visits):
	key = str(day)
	cell = {"s": "idle", "title": _("Idle"), "lines": [_("No tour, event or leave planned")]}

	# 1. Leave (red)
	for lv in leaves.get(person.employee, []):
		if getdate(lv.from_date) <= day <= getdate(lv.to_date):
			half = bool(lv.half_day and (not lv.half_day_date or getdate(lv.half_day_date) == day))
			pending = lv.docstatus == 0
			lines = [
				_("Leave Type: {0}").format(lv.leave_type),
				_("From {0} to {1} ({2} day(s))").format(
					formatdate(lv.from_date), formatdate(lv.to_date), lv.total_leave_days
				),
			]
			if half:
				lines.append(_("Half day"))
			if pending:
				lines.append(_("Waiting for approval"))
			if lv.description:
				lines.append(cstr(lv.description)[:120])
			cell = {
				"s": "leave",
				"title": _("On Leave") + (" (" + _("pending") + ")" if pending else ""),
				"lines": lines,
				"doctype": "Leave Application",
				"docname": lv.name,
				"half": 1 if half else 0,
				"pending": 1 if pending else 0,
			}
			return cell

	# 2. PJP (green tour / blue event / work)
	day_plans = [
		p for p in plans.get(person.name, []) if getdate(p.from_date) <= day <= getdate(p.to_date)
	]
	if day_plans:
		# a Tour wins over an Event when both overlap
		day_plans.sort(key=lambda p: 0 if p.plan_type == "Tour" else 1)
		p = day_plans[0]
		is_tour = p.plan_type == "Tour"
		lines = [
			_("{0}: {1}").format(_("Plan"), p.name),
			_("{0} to {1}").format(formatdate(p.from_date), formatdate(p.to_date)),
		]
		if p.territory:
			lines.append(_("Territory: {0}").format(p.territory))
		if p.docstatus == 0:
			lines.append(_("Draft: not approved yet"))
		today_visits = visits.get(p.name, {}).get(key, [])
		for v in today_visits[:6]:
			who = ", ".join(filter(None, [cstr(v.party_name), cstr(v.organization_name)]))
			bits = [who, f"({v.purpose})" if v.purpose else "", f"@ {v.destination}" if v.destination else ""]
			lines.append("• " + " ".join(b for b in bits if b))
		if len(today_visits) > 6:
			lines.append(_("+ {0} more").format(len(today_visits) - 6))
		cell = {
			"s": "tour" if is_tour else "work",
			"title": (_("On Tour") if is_tour else _("Event / Working"))
			+ (f" ({p.claim_mode})" if p.claim_mode == "Ad-Hoc Visit" else ""),
			"lines": lines,
			"doctype": "PJP Plan",
			"docname": p.name,
			"pending": 1 if p.docstatus == 0 else 0,
			"visits": len(today_visits),
		}
		if len(day_plans) > 1:
			cell["lines"].append(_("Also: {0}").format(", ".join(x.name for x in day_plans[1:])))
		return cell

	# 3. Holiday / weekly off (grey)
	hol = holidays.get(person.employee, {}).get(key) if person.employee else None
	if hol:
		return {
			"s": "holiday",
			"title": _("Weekly Off") if hol.weekly_off else _("Holiday"),
			"lines": [cstr(hol.description) or _("Holiday")],
		}

	if not person.employee:
		cell["lines"].append(_("No Employee is linked to this Sales Person, so leave cannot be checked."))
	return cell
