# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Sales Calendar: one row per Sales Person, one column per day.

Status of a day (first match wins):
  leave   (red)    approved / pending HRMS Leave Application
  tour    (green)  PJP Plan of type Tour (or an Ad-Hoc Visit) covering the day
  work    (blue)   PJP Plan of type Event covering the day
  holiday (grey)   holiday / weekly off in the employee's Holiday List
  work    (hours)  any other day with logged work: the day is split into 9 hourly slots
                   (9:00 to 18:00), each slot coloured by what was done in that hour
  idle    (yellow) a past working day with nothing logged at all
  future  (plain)  a day that has not happened yet and has nothing planned

Hourly data comes from
  * Timesheet (Timesheet Detail rows)       exact blocks, merged over several hours
  * Event (not all-day)                     blocks
  * Communication / Quotation / Sales Order auto markers ("suggested": a dot in that hour)
  * Employee Checkin                        slots outside first IN / last OUT are shown as "off"
"""

import datetime
import math

import frappe
from frappe.utils import add_days, cstr, date_diff, formatdate, get_datetime, getdate, now_datetime

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

# Working hours shown as hourly slots. Defaults; the real values are in "Sales Calendar Settings".
WORK_START = 10
WORK_END = 19


def get_work_hours():
	"""(start hour, end hour) from Sales Calendar Settings, falling back to the defaults."""
	start = end = None
	try:
		start = frappe.db.get_single_value("Sales Calendar Settings", "work_start")
		end = frappe.db.get_single_value("Sales Calendar Settings", "work_end")
	except Exception:
		pass
	start = int(start) if start not in (None, "") else WORK_START
	end = int(end) if end not in (None, "") else WORK_END
	if not (0 <= start < end <= 24 and 3 <= end - start <= 12):
		return WORK_START, WORK_END
	return start, end

# Activity Type (Timesheet) -> colour class of the slot. Created by install.py.
ACTIVITY_TYPES = {
	"Sales Call / Follow-up": "call",
	"Customer Visit": "visit",
	"Documentation": "doc",
	"Report / Analysis": "report",
	"Other Work": "work",
}
KIND_LABEL = {
	"call": "Sales call / Follow-up",
	"visit": "Customer visit",
	"doc": "Documentation",
	"report": "Report / Analysis",
	"work": "Other work",
	"auto": "Suggested (system activity)",
}
# an idle stretch shorter than this between two pieces of work is ignored
MIN_IDLE_MINUTES = 10
LOG_ROLES = {"System Manager", "Sales Manager", "HR Manager", "HR User"}


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
	activity = get_activity(persons, employees, start, end)
	now = now_datetime()

	rows = []
	summary = {
		str(d): {"leave": 0, "tour": 0, "work": 0, "holiday": 0, "idle": 0, "future": 0} for d in days
	}
	for p in persons:
		cells = {}
		for d in days:
			cell = build_cell(p, d, leaves, holidays, plans, visits, activity, now)
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
		"work_start": activity["start"],
		"work_end": activity["end"],
		"kinds": KIND_LABEL,
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
def build_cell(person, day, leaves, holidays, plans, visits, activity=None, now=None):
	key = str(day)
	cell = {"s": "idle", "title": _("Idle"), "lines": []}

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

	# 4. A normal working day: hourly slots from Timesheet / Event / system activity
	hours = hour_cell(person, day, activity or {}, now or now_datetime())
	if hours:
		return hours

	# 5. Nothing logged
	today = getdate((now or now_datetime()).date())
	is_future = day > today or (
		day == today and (now or now_datetime()).hour <= (activity or {}).get("start", WORK_START)
	)
	if is_future:
		return {"s": "future", "title": _("Not planned yet"), "lines": [_("No tour, event or leave planned")]}
	cell["lines"] = [_("Nothing logged: no Timesheet, call, quotation or event on this day")]
	if not person.employee:
		cell["lines"].append(_("No Employee is linked to this Sales Person, so leave cannot be checked."))
	return cell


# ----------------------------------------------------------------------
# Hourly activity
# ----------------------------------------------------------------------
def kind_of_activity(activity_type):
	"""Colour class of a Timesheet Activity Type (own types first, then by keyword)."""
	if activity_type in ACTIVITY_TYPES:
		return ACTIVITY_TYPES[activity_type]
	t = cstr(activity_type).lower()
	if "call" in t or "follow" in t or "phone" in t:
		return "call"
	if "visit" in t or "meeting" in t:
		return "visit"
	if "doc" in t:
		return "doc"
	if "report" in t or "analysis" in t:
		return "report"
	return "work"


def _hhmm(dt):
	return dt.strftime("%H:%M")


def get_activity(persons, employees, start, end):
	"""{"blocks": {person: [raw blocks]}, "points": {person: [raw points]}, "windows": {(person, date): (first_in, last_out)}}"""
	work_start, work_end = get_work_hours()
	out = {"blocks": {}, "points": {}, "windows": {}, "start": work_start, "end": work_end}
	if not employees:
		return out

	emp_rows = frappe.get_all("Employee", filters={"name": ["in", employees]}, fields=["name", "user_id"])
	emp_person = {p.employee: p.name for p in persons if p.employee}
	user_person = {}
	for e in emp_rows:
		if e.user_id and e.name in emp_person:
			user_person[e.user_id] = emp_person[e.name]
	users = list(user_person)

	t0 = datetime.datetime.combine(start, datetime.time.min)
	t1 = datetime.datetime.combine(end, datetime.time.max)

	def add_block(person, **kw):
		out["blocks"].setdefault(person, []).append(frappe._dict(kw))

	def add_point(person, when, text, doctype=None, docname=None):
		out["points"].setdefault(person, []).append(
			frappe._dict(at=get_datetime(when), text=text, doctype=doctype, docname=docname)
		)

	# 1. Timesheet (exact blocks)
	if frappe.db.exists("DocType", "Timesheet"):
		rows = frappe.db.sql(
			"""
			select t.name, t.employee, t.docstatus, d.activity_type, d.from_time, d.to_time,
				d.description, d.project
			from `tabTimesheet` t join `tabTimesheet Detail` d on d.parent = t.name
			where t.employee in %(emps)s and t.docstatus < 2
				and d.from_time <= %(t1)s and d.to_time >= %(t0)s
			""",
			{"emps": tuple(employees), "t0": t0, "t1": t1},
			as_dict=True,
		)
		for r in rows:
			if not (r.from_time and r.to_time):
				continue
			lines = []
			if r.description:
				lines.append(cstr(r.description)[:200])
			if r.project:
				lines.append(_("Project: {0}").format(r.project))
			lines.append(
				_("Timesheet {0}").format(r.name) + (" (" + _("Draft") + ")" if r.docstatus == 0 else "")
			)
			add_block(
				emp_person[r.employee],
				start=get_datetime(r.from_time),
				end=get_datetime(r.to_time),
				k=kind_of_activity(r.activity_type),
				title=cstr(r.activity_type) or _("Work"),
				lines=lines,
				doctype="Timesheet",
				docname=r.name,
			)

	if users:
		# 2. Calendar events (not all-day)
		for ev in frappe.get_all(
			"Event",
			filters={
				"owner": ["in", users],
				"starts_on": ["between", [t0, t1]],
				"status": ["!=", "Cancelled"],
			},
			fields=["name", "owner", "subject", "starts_on", "ends_on", "all_day", "event_category"],
		):
			if ev.all_day:
				continue
			st = get_datetime(ev.starts_on)
			en = get_datetime(ev.ends_on) if ev.ends_on else st + datetime.timedelta(hours=1)
			cat = cstr(ev.event_category)
			add_block(
				user_person[ev.owner],
				start=st,
				end=en,
				k="call" if cat in ("Call", "Sent/Received Email") else "visit" if cat == "Meeting" else "work",
				title=cstr(ev.subject) or _("Event"),
				lines=[_("Calendar Event {0}").format(ev.name)],
				doctype="Event",
				docname=ev.name,
			)

		# 3. System activity: one marker per document (time only, no duration)
		for c in frappe.get_all(
			"Communication",
			filters={
				"owner": ["in", users],
				"communication_type": "Communication",
				"creation": ["between", [t0, t1]],
			},
			fields=["name", "owner", "creation", "subject", "communication_medium", "reference_doctype", "reference_name"],
		):
			label = cstr(c.communication_medium) or _("Message")
			ref = f" ({c.reference_doctype} {c.reference_name})" if c.reference_name else ""
			add_point(
				user_person[c.owner], c.creation, f"{label}: {cstr(c.subject)[:60]}{ref}", "Communication", c.name
			)
		for dt in ("Quotation", "Sales Order"):
			for d in frappe.get_all(
				dt,
				filters={"owner": ["in", users], "creation": ["between", [t0, t1]], "docstatus": ["<", 2]},
				fields=["name", "owner", "creation", "title"],
			):
				add_point(user_person[d.owner], d.creation, f"{dt} {d.name}: {cstr(d.title)[:40]}", dt, d.name)

	# 4. Check-in / check-out window per day
	if frappe.db.exists("DocType", "Employee Checkin"):
		by_day = {}
		for c in frappe.get_all(
			"Employee Checkin",
			filters={"employee": ["in", employees], "time": ["between", [t0, t1]]},
			fields=["employee", "time", "log_type"],
			order_by="time asc",
		):
			when = get_datetime(c.time)
			by_day.setdefault((emp_person[c.employee], str(when.date())), []).append(when)
		for key, times in by_day.items():
			out["windows"][key] = (min(times), max(times))
	return out


def _fmt_dur(minutes):
	h, m = divmod(int(round(minutes)), 60)
	if h and m:
		return _("{0} hr {1} min").format(h, m)
	return _("{0} hr").format(h) if h else _("{0} min").format(m)


def hour_cell(person, day, activity, now):
	"""A working day as a minute-accurate timeline inside the working hours.

	Returns None when nothing at all was logged that day. Otherwise
	  timeline: segments {s, e, k, b} in minutes from the start of the working hours (Day / Week view)
	  slots:    one per hour with the same segments cut to 0..60 minutes (Month view)
	  blocks:   the real pieces of work (Timesheet / Event / suggested) the segments point to
	"""
	start_h = activity.get("start", WORK_START)
	end_h = activity.get("end", WORK_END)
	raw_blocks = activity.get("blocks", {}).get(person.name, [])
	raw_points = activity.get("points", {}).get(person.name, [])
	day_start = datetime.datetime.combine(day, datetime.time.min)
	day_end = day_start + datetime.timedelta(days=1)
	win_start = day_start + datetime.timedelta(hours=start_h)
	win_end = day_start + datetime.timedelta(hours=end_h)
	total = (end_h - start_h) * 60

	todays = [b for b in raw_blocks if b.start < win_end and b.end > win_start]
	points = [p for p in raw_points if win_start <= p.at < win_end]
	if not todays and not points:
		return None

	owner = [None] * total  # minute -> index in blocks
	blocks = []

	def new_block(b):
		st, en = max(b.start, day_start), min(b.end, day_end)
		minutes = (en - st).total_seconds() / 60
		blocks.append(
			{
				"k": b.k,
				"title": b.title,
				"lines": b.lines,
				"from": _hhmm(st),
				"to": "24:00" if en == day_end else _hhmm(en),
				"hours": round(minutes / 60, 2),
				"dur": _fmt_dur(minutes),
				"doctype": b.doctype,
				"docname": b.docname,
			}
		)
		return len(blocks) - 1

	# Timesheet first, then calendar events: where they overlap the Timesheet wins
	for b in sorted(todays, key=lambda x: (0 if x.doctype == "Timesheet" else 1, x.start)):
		i0 = max(0, int((max(b.start, win_start) - win_start).total_seconds() // 60))
		i1 = min(total, int(math.ceil((min(b.end, win_end) - win_start).total_seconds() / 60)))
		idx = None
		for i in range(i0, i1):
			if owner[i] is None:
				if idx is None:
					idx = new_block(b)
				owner[i] = idx

	# System activity: one "suggested" marker over the free minutes of that hour
	by_hour = {}
	for p in sorted(points, key=lambda x: x.at):
		by_hour.setdefault(int((p.at - win_start).total_seconds() // 3600), []).append(p)
	for hi, pts in by_hour.items():
		free = [i for i in range(hi * 60, min(total, hi * 60 + 60)) if owner[i] is None]
		if not free:
			continue
		h0 = win_start + datetime.timedelta(hours=hi)
		blocks.append(
			{
				"k": "auto",
				"title": _("Suggested: system activity"),
				"lines": [f"{_hhmm(p.at)}  {p.text}" for p in pts[:5]]
				+ ([_("+ {0} more").format(len(pts) - 5)] if len(pts) > 5 else []),
				"from": _hhmm(h0),
				"to": _hhmm(h0 + datetime.timedelta(hours=1)),
				"hours": round(len(free) / 60, 2),
				"dur": _fmt_dur(len(free)),
				"doctype": pts[0].doctype if len(pts) == 1 else None,
				"docname": pts[0].docname if len(pts) == 1 else None,
			}
		)
		for i in free:
			owner[i] = len(blocks) - 1

	if all(o is None for o in owner):
		return None

	# Minute -> (kind, block); free minutes are future / not in office / idle
	window = activity.get("windows", {}).get((person.name, str(day)))
	kinds = []
	for i in range(total):
		if owner[i] is not None:
			kinds.append((blocks[owner[i]]["k"], owner[i]))
			continue
		t = win_start + datetime.timedelta(minutes=i)
		if t >= now:
			kinds.append(("future", None))
		elif window and (t < window[0] or t >= window[1]):
			kinds.append(("off", None))
		else:
			kinds.append(("idle", None))

	segs = []
	for i, (k, b) in enumerate(kinds):
		if segs and segs[-1]["k"] == k and segs[-1]["b"] == b:
			segs[-1]["e"] = i + 1
		else:
			segs.append({"s": i, "e": i + 1, "k": k, "b": b})
	for sg in segs:
		# a gap of a few minutes between two pieces of work is not "idle"
		if sg["k"] == "idle" and sg["e"] - sg["s"] < MIN_IDLE_MINUTES:
			sg["k"] = "gap"

	slots = []
	for hi in range(end_h - start_h):
		a, z = hi * 60, (hi + 1) * 60
		parts = []
		for sg in segs:
			st, en = max(sg["s"], a), min(sg["e"], z)
			if en > st:
				parts.append({"s": st - a, "e": en - a, "k": sg["k"], "b": sg["b"]})
		dom = max(parts, key=lambda x: x["e"] - x["s"])
		slots.append({"h": start_h + hi, "k": dom["k"], "b": dom["b"], "segs": parts})

	logged = sum(1 for o in owner if o is not None and blocks[o]["k"] != "auto")
	return {
		"s": "work",
		"title": _("Working"),
		"lines": [],
		"timeline": segs,
		"total": total,
		"slots": slots,
		"blocks": blocks,
		"hours": round(logged / 60, 2),
		"logged_pct": round(100 * logged / total) if total else 0,
		"day_total": _("Logged {0} of {1}").format(_fmt_dur(logged), _fmt_dur(total)),
	}


# ----------------------------------------------------------------------
# Log an activity (creates / extends the day's draft Timesheet)
# ----------------------------------------------------------------------
@frappe.whitelist()
def log_activity(sales_person, activity_type, from_time, to_time, description=None):
	if activity_type not in ACTIVITY_TYPES:
		frappe.throw(_("Unknown activity type: {0}").format(activity_type))

	employee = frappe.db.get_value("Sales Person", sales_person, "employee")
	if not employee:
		frappe.throw(_("Sales Person {0} has no Employee linked.").format(sales_person))

	user_id, company = frappe.db.get_value("Employee", employee, ["user_id", "company"])
	if frappe.session.user != "Administrator" and frappe.session.user != user_id:
		if not (LOG_ROLES & set(frappe.get_roles())):
			frappe.throw(_("You can log activity only for yourself."), frappe.PermissionError)

	start, end = get_datetime(from_time), get_datetime(to_time)
	if end <= start:
		frappe.throw(_("To Time must be after From Time."))
	if start.date() != end.date():
		frappe.throw(_("From and To must be on the same day."))
	if start > now_datetime():
		frappe.throw(_("You cannot log time in the future."))

	row = {
		"activity_type": activity_type,
		"from_time": start,
		"to_time": end,
		"description": cstr(description).strip(),
	}
	name = frappe.db.get_value(
		"Timesheet", {"employee": employee, "docstatus": 0, "start_date": start.date()}, "name"
	)
	if name:
		ts = frappe.get_doc("Timesheet", name)
		ts.append("time_logs", row)
	else:
		ts = frappe.new_doc("Timesheet")
		ts.employee = employee
		ts.company = company
		ts.append("time_logs", row)
	ts.flags.ignore_permissions = True
	ts.save()
	return ts.name
