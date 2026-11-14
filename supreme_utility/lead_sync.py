# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Lead / Opportunity sync for PJP Plan visits (ERPNext / Frappe v16).

For every visit row with Visit With = "Lead" this module:

1. Links the row to an existing Lead, or creates a new one when none is found.
   A Lead is a PERSON (First Name) who works for a company (Organization Name). On the visit row
   the company is "Organization Name" (falls back to "Customer Name" when that is empty) and the
   person is "Met With" (falls back to "Customer Name"). One company can have many Leads.
   A person is matched by Lead ID, else by company + person name, else by company + mobile no.
2. Updates the Lead status from the Visit Result (never downgrades a Lead).
3. Writes the visit remarks / result remarks / meeting details into the Lead Notes
   (one note per visit, re-written on every sync, so no duplicates).
4. Fills blank Lead fields (mobile, designation, territory, campaign, owner) and creates a ToDo
   for the Lead owner on the "Next Follow Up Date" (v16 Leads have no Next Contact Date field).
5. Creates an Opportunity when the Visit Result is "Interested" (re-uses an open one if the
   Lead already has it) and links it back to the visit row.

It runs when the PJP is approved (submit) and every time it is updated after approval, and from
the "Sync Leads" button. It is idempotent: running it again creates no duplicates.
One failing row never blocks saving the PJP: the exact error is shown and written to the Error Log.
Every Lead row gets a line in the report (created / linked / unchanged / skipped / failed) so
nothing is silent.
"""

import frappe
from frappe.utils import cstr, escape_html, flt, formatdate, get_link_to_form, getdate, now_datetime, nowdate

from supreme_utility.messages import _

# Visit Result -> Lead status. None = leave the Lead status alone.
# (ERPNext itself sets Opportunity / Quotation / Lost Quotation / Converted from linked documents.)
LEAD_STATUS_BY_RESULT = {
	"Order": "Converted",
	"Interested": "Interested",
	"Follow-up": "Replied",
	"Not interested": "Do Not Contact",
	"Not met": None,
}

# A visit result may only move a Lead forward, never backward
LEAD_STATUS_RANK = {
	"Lead": 0,
	"Open": 1,
	"Do Not Contact": 1,
	"Replied": 2,
	"Interested": 3,
	"Lost Quotation": 3,
	"Opportunity": 4,
	"Quotation": 5,
	"Converted": 6,
}

OPPORTUNITY_RESULT = "Interested"
OPEN_OPPORTUNITY_STATUSES = ["Open", "Replied", "Quotation"]


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------
def sync_pjp_leads(pjp, notify=True):
	"""Sync all Lead visit rows of a submitted PJP Plan.

	Returns frappe._dict(events=[...], skipped=[...], failed=[...], unchanged=[...]) (all lists of text).
	"""
	report = frappe._dict(events=[], skipped=[], failed=[], unchanged=[])

	if not frappe.db.exists("DocType", "Lead"):
		return report

	lead_rows = [r for r in pjp.get("visits") or [] if r.visit_with == "Lead"]
	rows = []
	for r in lead_rows:
		if r.party or cstr(r.party_name).strip() or cstr(r.organization_name).strip():
			rows.append(r)
		else:
			report.skipped.append(
				_("Row #{0}: no Lead selected and no Customer Name typed, so no Lead could be created.").format(r.idx)
			)
	# oldest visit first, so a later result is the one that sticks
	rows.sort(key=lambda r: (cstr(r.actual_visit_date or r.planned_date), r.idx))

	for row in rows:
		save_point = f"pjp_lead_{row.idx}"
		frappe.db.savepoint(save_point)
		log_len = len(frappe.local.message_log)
		try:
			res = _sync_row(pjp, row)
		except Exception as e:
			frappe.db.rollback(save_point=save_point)
			# the real reason is in the exception; drop the half-baked popups it queued
			del frappe.local.message_log[log_len:]
			error_log = frappe.log_error(
				title=_("PJP Lead sync failed: {0} row {1}").format(pjp.name, row.idx),
				message=frappe.get_traceback(),
			)
			report.failed.append(
				_("Row #{0}: {1}").format(row.idx, strip_html(cstr(e))[:300] or e.__class__.__name__)
			)
			continue

		# keep the in-memory document in step with what was written (the form reloads from it)
		for fieldname, value in res.row_updates.items():
			row.set(fieldname, value)
		if res.events:
			report.events.extend(res.events)
		else:
			report.unchanged.append(_("Row #{0}: {1} is up to date").format(row.idx, res.lead_label))

	if report.events:
		pjp.add_comment("Info", "\n".join(strip_html(e) for e in report.events))

	if notify:
		show_report(report, has_lead_rows=bool(lead_rows))
	return report


def show_report(report, has_lead_rows=True):
	"""One popup with everything that happened (nothing is silent)."""
	blocks = []
	if report.events:
		blocks.append(_block(_("Done"), report.events, "green", escape=False))
	if report.failed:
		blocks.append(
			_block(_("Failed (see Error Log for details)"), report.failed, "red")
		)
	if report.skipped:
		blocks.append(_block(_("Skipped"), report.skipped, "orange"))
	if not blocks:
		return
	indicator = "red" if report.failed else ("green" if report.events else "orange")
	frappe.msgprint("<br>".join(blocks), title=_("Leads from PJP"), indicator=indicator)


def _block(title, lines, colour, escape=True):
	body = "".join(f"<li>{escape_html(x) if escape else x}</li>" for x in lines)
	return f"<div class='mb-3'><b class='text-{'success' if colour == 'green' else 'danger' if colour == 'red' else 'warning'}'>{escape_html(title)}</b><ul>{body}</ul></div>"


@frappe.whitelist()
def sync_leads(pjp_name):
	"""Manual re-sync (button on the PJP form). Also back-fills PJPs approved before this feature."""
	pjp = frappe.get_doc("PJP Plan", pjp_name)
	pjp.check_permission("write")
	if pjp.docstatus != 1:
		frappe.throw(_("Leads are synced once the PJP is approved (submitted)."))
	if not any(r.visit_with == "Lead" for r in pjp.visits):
		frappe.msgprint(
			_("This PJP has no visit with Visit With = Lead. Leads are created only for those visits."),
			indicator="orange",
		)
		return False
	report = sync_pjp_leads(pjp, notify=False)
	if not (report.events or report.failed or report.skipped):
		frappe.msgprint(_("Leads are already up to date.") + "<br>" + "<br>".join(escape_html(x) for x in report.unchanged), indicator="blue")
	else:
		show_report(report)
	return True


# ----------------------------------------------------------------------
# One visit row
# ----------------------------------------------------------------------
def _sync_row(pjp, row):
	res = frappe._dict(row_updates={}, events=[], lead_label="")

	lead_name = find_lead(row)
	if lead_name:
		lead = frappe.get_doc("Lead", lead_name)
		if lead.name != row.party:
			res.events.append(
				_("Row #{0}: linked to existing Lead {1}").format(row.idx, lead_label(lead))
			)
	else:
		lead = create_lead(pjp, row)
		res.events.append(
			_("Row #{0}: new Lead {1} created").format(row.idx, lead_label(lead))
		)

	old_status = lead.status
	update_lead(lead, pjp, row)
	lead.flags.ignore_permissions = True
	lead.save()
	res.lead_label = lead_label(lead)
	ensure_follow_up_todo(lead, pjp, row, res)
	if lead.status != old_status and lead_name:
		res.events.append(
			_("Row #{0}: Lead {1} status {2} to {3}").format(
				row.idx, lead_label(lead), old_status, lead.status
			)
		)

	# Opportunity is created AFTER the Lead is saved: ERPNext then moves the Lead to "Opportunity"
	opportunity = row.opportunity if row.opportunity and frappe.db.exists("Opportunity", row.opportunity) else None
	if row.visit_result == OPPORTUNITY_RESULT or opportunity:
		opportunity, created = ensure_opportunity(pjp, row, lead, opportunity)
		if created:
			res.events.append(
				_("Row #{0}: Opportunity {1} created").format(row.idx, get_link_to_form("Opportunity", opportunity))
			)

	updates = {}
	if row.party != lead.name:
		updates["party"] = lead.name
	# Customer Name = the Lead (person); Organization Name = the Lead's company
	if row.party_name != lead.lead_name:
		updates["party_name"] = lead.lead_name
	organization = lead.get("company_name") or get_company(row)
	if organization and row.organization_name != organization:
		updates["organization_name"] = organization
	if opportunity and row.opportunity != opportunity:
		updates["opportunity"] = opportunity
	if updates:
		frappe.db.set_value("PJP Visit", row.name, updates, update_modified=False)
	res.row_updates = updates
	return res


def lead_label(lead):
	"""Link to the Lead plus 'person, company'."""
	who = ", ".join(filter(None, [cstr(lead.get("lead_name")), cstr(lead.get("company_name"))]))
	return f"{get_link_to_form('Lead', lead.name)} ({escape_html(who)})"


# ----------------------------------------------------------------------
# Lead: find / create / update
# ----------------------------------------------------------------------
def get_company(row):
	"""Company of the visit: Organization Name, else the typed Customer Name."""
	return cstr(row.organization_name).strip() or cstr(row.party_name).strip()


def get_person_name(row):
	"""Person met: Met With, else Customer Name, else the company.
	A designation typed in the name column does not count as a name."""
	company = get_company(row)
	designation = cstr(row.designation).strip().lower()
	for name in (cstr(row.met_with).strip(), cstr(row.party_name).strip()):
		if name and name.lower() != designation and name.lower() != company.lower():
			return name
	return company


def find_lead(row):
	if row.party and frappe.db.exists("Lead", row.party):
		return row.party

	company = get_company(row)
	if not company:
		return None
	person = get_person_name(row).lower()
	contact_no = cstr(row.contact_no).strip()

	# Only people of the same company are candidates
	candidates = frappe.get_all(
		"Lead",
		filters={"company_name": company},
		fields=["name", "lead_name", "mobile_no", "phone"],
		order_by="modified desc",
	)
	for c in candidates:
		if cstr(c.lead_name).strip().lower() == person:
			return c.name
	if contact_no:
		for c in candidates:
			if contact_no in (cstr(c.mobile_no), cstr(c.phone)):
				return c.name
	return None


def create_lead(pjp, row):
	company = get_company(row)
	lead = frappe.new_doc("Lead")
	lead.first_name = get_person_name(row)  # the person
	lead.flags.ignore_permissions = True
	for fieldname, value in (
		("company_name", company),  # Organization Name
		("mobile_no", row.contact_no),
		("job_title", row.designation),
		("territory", pjp.territory),
		("utm_campaign", get_utm_campaign(pjp)),
		("company", pjp.company),
		("lead_owner", get_owner_user(pjp)),
	):
		set_field(lead, fieldname, value)
	lead.insert()
	return lead


def update_lead(lead, pjp, row):
	# 1. Fill blanks only. Existing Lead data is never overwritten.
	for fieldname, value in (
		("company_name", get_company(row)),
		("mobile_no", row.contact_no),
		("job_title", row.designation),
		("territory", pjp.territory),
		("utm_campaign", get_utm_campaign(pjp)),
		("company", pjp.company),
		("lead_owner", get_owner_user(pjp)),
	):
		if not lead.get(fieldname):
			set_field(lead, fieldname, value)

	# 2. Status moves forward only
	new_status = get_new_lead_status(lead.status, row.visit_result)
	if new_status:
		lead.status = new_status

	# 3. Notes (description of the visit)
	note = build_note(pjp, row, lead)
	if note:
		upsert_note(lead, f"{pjp.name}/{row.name}", note)


def get_new_lead_status(current, result):
	"""The status a visit result moves a Lead to, or None when it should stay as it is."""
	new = LEAD_STATUS_BY_RESULT.get(result)
	if not new or new == current:
		return None
	current_rank = LEAD_STATUS_RANK.get(current, 0)
	if new == "Do Not Contact":
		# never write a Lead off once it is a live Opportunity / Quotation / customer
		return new if current_rank < LEAD_STATUS_RANK["Opportunity"] else None
	return new if LEAD_STATUS_RANK.get(new, 0) > current_rank else None


# ----------------------------------------------------------------------
# Opportunity
# ----------------------------------------------------------------------
def ensure_opportunity(pjp, row, lead, opportunity=None):
	"""Returns (opportunity name, created?)."""
	if not opportunity:
		opportunity = frappe.db.get_value(
			"Opportunity",
			{
				"opportunity_from": "Lead",
				"party_name": lead.name,
				"status": ["in", OPEN_OPPORTUNITY_STATUSES],
			},
			"name",
			order_by="modified desc",
		)

	note = build_note(pjp, row, lead)
	marker = f"{pjp.name}/{row.name}"

	if opportunity:
		opp = frappe.get_doc("Opportunity", opportunity)
		if note:
			upsert_note(opp, marker, note)
			opp.flags.ignore_permissions = True
			opp.save()
		return opportunity, False

	opp = frappe.new_doc("Opportunity")
	opp.opportunity_from = "Lead"
	opp.party_name = lead.name
	opp.company = pjp.company
	opp.status = "Open"
	opp.transaction_date = nowdate()
	for fieldname, value in (
		("territory", pjp.territory or lead.get("territory")),
		("utm_campaign", get_utm_campaign(pjp) or lead.get("utm_campaign")),
		("utm_source", lead.get("utm_source")),
		("job_title", lead.get("job_title")),
		("opportunity_owner", lead.get("lead_owner") or get_owner_user(pjp)),
		("opportunity_amount", flt(row.order_value) or flt(row.expected_business) or None),
	):
		set_field(opp, fieldname, value)
	if row.next_follow_up_date and getdate(row.next_follow_up_date) >= getdate(nowdate()):
		set_field(opp, "expected_closing", row.next_follow_up_date)
	if note:
		upsert_note(opp, marker, note)
	opp.flags.ignore_permissions = True
	opp.insert()
	return opp.name, True


# ----------------------------------------------------------------------
# Notes
# ----------------------------------------------------------------------
def build_note(pjp, row, lead=None):
	"""HTML note for one visit. Returns None when the visit has nothing worth noting yet."""
	if not (row.visit_remarks or row.result_remarks or row.visit_result or row.actual_visit_date):
		return None

	visit_date = row.actual_visit_date or row.planned_date
	pairs = [
		(_("Visit date"), formatdate(visit_date) if visit_date else None),
		(_("Time period"), row.visit_time),
		(_("Destination"), row.destination),
		(_("Met with"), " ".join(filter(None, [row.met_with, f"({row.designation})" if row.designation else None]))),
		(_("Contact no"), row.contact_no),
		(_("Purpose"), row.purpose),
		(_("Visit result"), row.visit_result),
		(_("Reason for not interested"), row.not_interested_reason),
		(_("Order value"), frappe.format_value(row.order_value, {"fieldtype": "Currency"}) if flt(row.order_value) else None),
		(_("Sales Order"), row.sales_order),
		(_("Next follow up"), formatdate(row.next_follow_up_date) if row.next_follow_up_date else None),
		(_("Visit remarks"), row.visit_remarks),
		(_("Result remarks"), row.result_remarks),
	]
	# The Lead already has a different number: keep both, overwrite nothing
	if lead and lead.get("mobile_no") and row.contact_no and cstr(lead.mobile_no) != cstr(row.contact_no):
		pairs.append((_("Different contact no given in visit"), row.contact_no))

	head = _("PJP {0} ({1}, {2}) by {3}").format(pjp.name, pjp.plan_type, row.purpose or "", pjp.salesperson)
	body = "<br>".join(f"<b>{escape_html(k)}:</b> {escape_html(cstr(v))}" for k, v in pairs if v)
	ref = f"<span class='text-muted'>Ref: {escape_html(pjp.name)}/{escape_html(row.name)}</span>"
	return f"<div><b>{escape_html(head)}</b><br>{body}<br>{ref}</div>"


def upsert_note(doc, marker, html):
	"""Update the note of this visit if it exists, else add it. Never duplicates."""
	marker_text = f"Ref: {marker}"
	if doc.meta.has_field("notes"):
		for n in doc.get("notes") or []:
			if marker_text in cstr(n.note):
				n.note = html
				return
		doc.append("notes", {"note": html, "added_by": frappe.session.user, "added_on": now_datetime()})
		return

	# No notes table on this version: fall back to a comment (once per visit)
	if not frappe.db.exists(
		"Comment",
		{
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"comment_type": "Comment",
			"content": ["like", f"%{marker_text}%"],
		},
	):
		doc.add_comment("Comment", html)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def set_field(doc, fieldname, value):
	"""Set a field only if it exists on this version and the value is not empty."""
	if value not in (None, "", 0) and doc.meta.has_field(fieldname):
		doc.set(fieldname, value)


def get_utm_campaign(pjp):
	"""v16 Leads / Opportunities link to a UTM Campaign. ERPNext creates one with the same name as
	each Campaign; use it only if it exists."""
	campaign = pjp.get("campaign")
	if campaign and frappe.db.exists("DocType", "UTM Campaign") and frappe.db.exists("UTM Campaign", campaign):
		return campaign
	return None


def ensure_follow_up_todo(lead, pjp, row, res):
	"""One open ToDo per visit for the Next Follow Up Date (v16 Leads have no contact date field)."""
	if not row.next_follow_up_date or getdate(row.next_follow_up_date) < getdate(nowdate()):
		return
	marker = f"Ref: {pjp.name}/{row.name}"
	remarks = escape_html(cstr(row.result_remarks or row.visit_remarks))
	description = (
		escape_html(_("Follow up with {0} ({1})").format(lead.lead_name, lead.get("company_name") or ""))
		+ (f"<br>{remarks}" if remarks else "")
		+ f"<br><span class='text-muted'>{escape_html(marker)}</span>"
	)
	owner = lead.get("lead_owner") or get_owner_user(pjp) or frappe.session.user

	existing = frappe.db.get_value(
		"ToDo",
		{
			"reference_type": "Lead",
			"reference_name": lead.name,
			"status": "Open",
			"description": ["like", f"%{marker}%"],
		},
		["name", "date"],
		as_dict=True,
	)
	if existing:
		if cstr(existing.date) != cstr(row.next_follow_up_date):
			frappe.db.set_value("ToDo", existing.name, {"date": row.next_follow_up_date, "description": description})
			res.events.append(
				_("Row #{0}: follow-up ToDo moved to {1}").format(row.idx, formatdate(row.next_follow_up_date))
			)
		return

	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": owner,
			"reference_type": "Lead",
			"reference_name": lead.name,
			"date": row.next_follow_up_date,
			"description": description,
			"priority": "Medium",
			"status": "Open",
		}
	)
	todo.flags.ignore_permissions = True
	todo.insert()
	res.events.append(
		_("Row #{0}: follow-up ToDo created for {1}").format(row.idx, formatdate(row.next_follow_up_date))
	)


def get_owner_user(pjp):
	if pjp.employee:
		return frappe.db.get_value("Employee", pjp.employee, "user_id")
	return None


def strip_html(text):
	return frappe.utils.strip_html(text)
