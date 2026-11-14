# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import cstr, escape_html, flt, formatdate, get_datetime, getdate, nowdate

from supreme_utility.messages import _

PARTY_NAME_FIELD = {
	"Customer": "customer_name",
	"Lead": "lead_name",
	"Contact": "full_name",
}

# Fields on a visit row that belong to the "actual" stage
ACTUAL_VISIT_FIELDS = (
	"actual_visit_date",
	"met_with",
	"visit_result",
	"result_remarks",
	"sales_order",
	"opportunity",
	"order_value",
	"next_follow_up_date",
	"not_interested_reason",
)

PRIVILEGED_ROLES = {"System Manager", "Sales Manager", "Accounts User", "Accounts Manager"}

PJP_MODE = "PJP Plan"
CLAIM_ONLY = "Ad-Hoc Visit"

# Fields compared to lock an approved Ad-Hoc Visit document
CLAIM_VISIT_FIELDS = (
	"visit_with", "party", "party_name", "organization_name", "purpose", "visit_remarks", "visit_time", "met_with",
	"designation", "contact_no", "destination", "visit_result", "order_value", "sales_order", "opportunity",
	"next_follow_up_date", "not_interested_reason", "result_remarks", "actual_visit_date",
)
CLAIM_COST_FIELDS = (
	"cost_head", "expense_date", "travel_mode", "from_place", "to_place", "actual_amount",
	"description", "receipt",
)


def _norm(value):
	"""Comparable form of a field value.

	The form sends 1500 (int) while the database returns 1500.0 (float), and None / "" / 0 are all
	"empty" for the user, so a plain cstr() comparison reports changes that nobody made.
	"""
	if value is None or value == "":
		return ""
	if isinstance(value, (int, float, Decimal)):
		return "" if not value else repr(round(float(value), 6))
	return cstr(value).replace("\r\n", "\n").strip()


def get_purposes(pjp):
	"""Distinct visit purposes of a PJP, in visit order (Purpose now lives on each visit row)."""
	seen = []
	for row in pjp.get("visits") or []:
		if row.purpose and row.purpose not in seen:
			seen.append(row.purpose)
	return ", ".join(seen)


def _first_row_change(new_rows, old_rows, fields, label):
	"""Describe the first real difference between two child tables, or None if they are the same."""
	if len(new_rows) != len(old_rows):
		return _("{0}: rows were added or removed").format(label)
	old = {r.name: r for r in old_rows}
	for row in new_rows:
		prev = old.get(row.name)
		if not prev:
			return _("{0}: row #{1} is new").format(label, row.idx)
		for f in fields:
			if _norm(row.get(f)) != _norm(prev.get(f)):
				return _("{0} row #{1}: {2} changed from '{3}' to '{4}'").format(
					label, row.idx, f, escape_html(cstr(prev.get(f))), escape_html(cstr(row.get(f)))
				)
	return None


class PJPPlan(Document):
	# ------------------------------------------------------------------
	# Lifecycle
	# ------------------------------------------------------------------
	def is_claim_only(self):
		return self.claim_mode == CLAIM_ONLY

	def validate(self):
		self.run_validations()

	def run_validations(self):
		"""All checks. Frappe v16 runs only before_update_after_submit (not validate) when an approved
		document is updated, so both hooks call this."""
		self.set_company()
		self.set_claim_mode_defaults()
		self.validate_claim_only_locked()
		self.mark_unplanned_visits()
		self.validate_dates()
		self.set_party_names()
		self.validate_visits()
		self.validate_actual_entry_stage()
		self.validate_claim_only_advance()
		self.validate_actuals()
		self.validate_not_closed()
		self.validate_visit_rows_unchanged()
		self.calculate_totals()
		self.set_status()

	def before_submit(self):
		if self.is_claim_only():
			# Submit = approval by the Sales Manager (only Sales Manager / System Manager hold the
			# Submit permission). Visits and expenses are the proof, so both are mandatory.
			if not self.visits:
				frappe.throw(_("Add at least one visit before submitting for approval."))
			if not self.actual_costs:
				frappe.throw(_("Add at least one expense row before submitting for approval."))
			return
		# Validation 2: at least one visit row and one estimate row before approval.
		# (Open point in the design doc: relax the visit rule for Events if needed.)
		if not self.visits:
			frappe.throw(_("Add at least one visit before approving the plan."))
		if not self.estimate_costs:
			frappe.throw(_("Add at least one estimated cost row before approving the plan."))

	def on_submit(self):
		self.sync_leads()

	def on_update_after_submit(self):
		self.sync_leads()

	def sync_leads(self):
		"""Link / create Leads for 'Lead' visits, update status and notes, create Opportunities."""
		from supreme_utility.lead_sync import sync_pjp_leads

		return sync_pjp_leads(self)

	def before_update_after_submit(self):
		# Actual Start, Actual End and Travel Mode are mandatory once the plan is approved
		missing = [
			label
			for fn, label in (
				("actual_start", _("Actual Start")),
				("actual_end", _("Actual End")),
				("travel_mode", _("Travel Mode")),
			)
			if not self.get(fn)
		]
		if missing:
			frappe.throw(_("Please fill in Actual Trip Details: {0}").format(", ".join(missing)))

		self.run_validations()

	def before_cancel(self):
		if self.is_claim_only():
			# Not yet claimed: the Sales Manager can cancel so the salesperson can amend and resubmit
			if self.expense_claim:
				frappe.throw(_("Expense Claim {0} is linked. Cancel it first.").format(self.expense_claim))
			self.status = "Cancelled"
			return
		if self.employee_advance:
			frappe.throw(_("Employee Advance {0} is linked. Cancel it first.").format(self.employee_advance))
		# Cancelled = plan cancelled before any actuals are entered
		if self.expense_claim or self.actual_costs or self.has_actual_visit_data():
			frappe.throw(_("A PJP with actual details cannot be cancelled."))
		self.status = "Cancelled"

	# ------------------------------------------------------------------
	# Validations
	# ------------------------------------------------------------------
	def set_company(self):
		# Company drives "Delete Transactions": documents with a Company link are included
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company")
		if self.company and self.employee:
			emp_company = frappe.db.get_value("Employee", self.employee, "company")
			if emp_company and emp_company != self.company:
				frappe.throw(
					_("Employee {0} belongs to company {1}, not {2}.").format(
						self.employee, emp_company, self.company
					)
				)

	def set_claim_mode_defaults(self):
		if not self.claim_mode:
			self.claim_mode = PJP_MODE  # records created before Claim Mode existed
		if not self.is_claim_only():
			return
		# The trip is already over: the claim period is the actual trip period
		if self.from_date and self.to_date:
			self.actual_start = f"{getdate(self.from_date)} 00:00:00"
			self.actual_end = f"{getdate(self.to_date)} 23:59:59"
		if not self.travel_mode:
			self.travel_mode = "Various"

	def validate_claim_only_locked(self):
		"""After the Sales Manager approves (submit), visits and expenses cannot be edited."""
		if not self.is_claim_only() or self.docstatus != 1:
			return
		before = self.get_doc_before_save()
		# Draft -> Submit is the approval itself: whatever is on the form is what gets approved.
		# Only edits to an already approved document are locked.
		if not before or before.docstatus != 1:
			return
		change = _first_row_change(
			self.visits, before.visits, CLAIM_VISIT_FIELDS, _("Visits")
		) or _first_row_change(self.actual_costs, before.actual_costs, CLAIM_COST_FIELDS, _("Expenses"))
		if change:
			frappe.throw(
				_("This claim is approved, so visits and expenses cannot be changed. Cancel it, amend and submit again.")
				+ "<br><br>"
				+ change
			)

	def validate_claim_only_advance(self):
		# Optional existing advance, chosen before approval
		if not (self.is_claim_only() and self.docstatus == 0 and self.employee_advance):
			return
		adv = frappe.db.get_value(
			"Employee Advance", self.employee_advance, ["employee", "docstatus"], as_dict=True
		)
		if not adv or adv.employee != self.employee:
			frappe.throw(_("Employee Advance {0} does not belong to employee {1}.").format(self.employee_advance, self.employee))
		if adv.docstatus != 1:
			frappe.throw(_("Employee Advance {0} must be submitted (and paid) before it can be adjusted.").format(self.employee_advance))

	def validate_dates(self):
		# Validation 1
		if self.from_date and self.to_date and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To Date cannot be before From Date."))

	def mark_unplanned_visits(self):
		"""Visit rows added after approval were not part of the plan: flag them."""
		if self.docstatus != 1:
			return
		before = self.get_doc_before_save()
		if not before:
			return
		existing = {v.name for v in before.visits}
		for row in self.visits:
			if row.name not in existing:
				row.is_unplanned = 1
				row.planned_date = None
				row.expected_business = 0

	def set_party_names(self):
		for row in self.visits:
			# After approval only new (unplanned) rows may still change
			if self.docstatus == 1 and not row.is_unplanned:
				continue
			field = PARTY_NAME_FIELD.get(row.visit_with)
			if field and row.party:
				# A linked record always decides the name
				row.party_name = frappe.db.get_value(row.visit_with, row.party, field)
				if row.visit_with == "Lead":
					# the Lead is the person; its company goes to Organization Name
					row.organization_name = frappe.db.get_value("Lead", row.party, "company_name") or row.organization_name
			# Lead without a linked record: keep the name typed by the salesperson

	def validate_visits(self):
		for row in self.visits:
			# Validation 4: Customer / Contact need a linked party.
			# A Lead needs either a linked Lead or a typed Party Name (no record creation required).
			if not row.visit_with:
				frappe.throw(_("Row #{0}: Visit With is mandatory.").format(row.idx))
			if row.visit_with == "Other":
				if not (row.party_name or "").strip():
					frappe.throw(_("Row #{0}: Type the Customer Name.").format(row.idx))
			elif row.visit_with == "Lead":
				if not (row.party or (row.party_name or "").strip()):
					frappe.throw(_("Row #{0}: Select a Lead in Customer ID or type the Customer Name.").format(row.idx))
			elif not row.party:
				frappe.throw(_("Row #{0}: Customer ID is mandatory for {1}.").format(row.idx, row.visit_with))

			# Unplanned visits (added during/after the trip) need no planned date / expected business.
			# Ad-Hoc Visit visits are all actual, so they have no planned date either.
			if row.is_unplanned or self.is_claim_only():
				continue

			if not row.planned_date:
				frappe.throw(_("Row #{0}: Planned Date is mandatory.").format(row.idx))
			# Validation 3
			if self.from_date and self.to_date:
				if not (getdate(self.from_date) <= getdate(row.planned_date) <= getdate(self.to_date)):
					frappe.throw(
						_("Row #{0}: Planned Date must fall between From Date and To Date.").format(row.idx)
					)

	def has_actual_visit_data(self):
		return any(row.get(fn) for row in self.visits for fn in ACTUAL_VISIT_FIELDS)

	def validate_actual_entry_stage(self):
		# Validation 5: actuals only after the plan is approved (Planned).
		# Ad-Hoc Visit enters actuals up front, the approval comes afterwards.
		if self.is_claim_only():
			return
		if self.docstatus == 0 and (
			self.actual_costs or self.actual_start or self.actual_end or self.has_actual_visit_data()
		):
			frappe.throw(_("Actual details can be entered only after the plan is approved."))

	def validate_actuals(self):
		today = getdate(nowdate())

		# Validation 6
		for row in self.visits:
			if row.actual_visit_date and getdate(row.actual_visit_date) > today:
				frappe.throw(_("Row #{0}: Actual Date of Visit cannot be in the future.").format(row.idx))

		start = get_datetime(self.actual_start) if self.actual_start else None
		end = get_datetime(self.actual_end) if self.actual_end else None
		if self.actual_costs and not (start and end):
			frappe.throw(
				_("Enter Actual Start and Actual End first. Expense dates must fall within the trip dates.")
			)
		if start and end and end < start:
			frappe.throw(_("Actual End cannot be before Actual Start."))

		# Actual Date of Visit must fall within the actual trip dates
		for row in self.visits:
			if (row.is_unplanned or self.is_claim_only()) and not row.actual_visit_date:
				frappe.throw(_("Row #{0}: Enter the Actual Date of Visit.").format(row.idx))
			if not row.actual_visit_date:
				continue
			if not (start and end):
				frappe.throw(
					_("Row #{0}: Enter Actual Start and Actual End before the Actual Date of Visit.").format(row.idx)
				)
			visit_day = getdate(row.actual_visit_date)
			if visit_day < start.date() or visit_day > end.date():
				frappe.throw(
					_("Row #{0}: Actual Date of Visit must fall within the actual trip dates ({1} to {2}).").format(
						row.idx, formatdate(start.date()), formatdate(end.date())
					)
				)

		for row in self.actual_costs:
			if flt(row.actual_amount) <= 0:
				frappe.throw(_("Actual Cost row #{0}: Actual Amount must be greater than zero.").format(row.idx))
			if row.expense_date:
				exp = getdate(row.expense_date)
				if exp > today:
					frappe.throw(_("Actual Cost row #{0}: Expense Date cannot be in the future.").format(row.idx))
				if start and exp < start.date():
					frappe.throw(_("Actual Cost row #{0}: Expense Date is before Actual Start.").format(row.idx))
				if end and exp > end.date():
					frappe.throw(_("Actual Cost row #{0}: Expense Date is after Actual End.").format(row.idx))

	def validate_not_closed(self):
		# Closed = read-only (Expense Claim already raised)
		if self.docstatus != 1:
			return
		before = self.get_doc_before_save()
		if before and before.expense_claim:
			frappe.throw(
				_("This PJP is Closed (Expense Claim {0}). Cancel the claim to reopen it.").format(
					before.expense_claim
				)
			)

	def validate_visit_rows_unchanged(self):
		# After approval: planned rows are locked. New (unplanned) rows may be added, and an
		# unplanned row may be removed again, but a planned row can never be removed or edited.
		if self.docstatus != 1:
			return
		before = self.get_doc_before_save()
		if not before:
			return
		old_rows = {v.name: v for v in before.visits}
		current = {v.name for v in self.visits}

		removed_planned = [n for n, r in old_rows.items() if n not in current and not r.is_unplanned]
		if removed_planned:
			frappe.throw(_("Planned visit rows cannot be removed after the plan is approved."))

		for row in self.visits:
			old = old_rows.get(row.name)
			if not old:
				continue
			changed = any(
				cstr(row.get(fn)) != cstr(old.get(fn))
				for fn in ("visit_with", "party", "party_name", "organization_name", "purpose", "planned_date", "visit_remarks")
			) or flt(row.expected_business) != flt(old.expected_business)
			if changed:
				frappe.throw(
					_("Row #{0}: Visit details cannot be changed after approval. Add an unplanned visit instead.").format(
						row.idx
					)
				)

	def calculate_totals(self):
		self.total_estimated_cost = sum(flt(r.estimated_amount) for r in self.estimate_costs)
		self.total_expected_business = sum(flt(r.expected_business) for r in self.visits)
		self.total_actual_cost = sum(flt(r.actual_amount) for r in self.actual_costs)
		self.total_order_value = sum(flt(r.order_value) for r in self.visits)
		# Business generated by visits that were not part of the plan (already included in Order Value)
		self.total_unplanned_business = sum(flt(r.order_value) for r in self.visits if r.is_unplanned)

		if self.is_claim_only():
			# No estimate exists, so there is no variance; ROI is still calculated
			self.cost_variance = 0
			self.roi_percent = (
				flt((flt(self.total_order_value) - flt(self.total_actual_cost)) / flt(self.total_actual_cost) * 100, 2)
				if self.total_actual_cost
				else None
			)
		elif self.total_actual_cost:
			self.cost_variance = flt(self.total_estimated_cost) - flt(self.total_actual_cost)
			self.roi_percent = flt(
				(flt(self.total_order_value) - flt(self.total_actual_cost))
				/ flt(self.total_actual_cost)
				* 100,
				2,
			)
		else:
			# No actual cost: variance is meaningless and ROI is left blank (no divide by zero)
			self.cost_variance = 0
			self.roi_percent = None

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 2:
			self.status = "Cancelled"
		elif self.expense_claim:
			self.status = "Closed"
		elif self.actual_costs:
			self.status = "Visited"
		else:
			self.status = "Planned"


# ----------------------------------------------------------------------
# Permissions (Sales User sees only their own PJPs)
# ----------------------------------------------------------------------
def is_privileged(user):
	return user == "Administrator" or bool(PRIVILEGED_ROLES & set(frappe.get_roles(user)))


def get_user_employees(user):
	return frappe.get_all("Employee", filters={"user_id": user}, pluck="name")


def get_user_sales_persons(user):
	employees = get_user_employees(user)
	if not employees:
		return []
	return frappe.get_all("Sales Person", filters={"employee": ["in", employees]}, pluck="name")


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if is_privileged(user):
		return ""
	parts = []
	persons = get_user_sales_persons(user)
	if persons:
		parts.append("`tabPJP Plan`.`salesperson` in ({0})".format(", ".join(frappe.db.escape(p) for p in persons)))
	employees = get_user_employees(user)
	if employees:
		parts.append("`tabPJP Plan`.`employee` in ({0})".format(", ".join(frappe.db.escape(e) for e in employees)))
	# Always allow documents the user created themselves
	parts.append(f"`tabPJP Plan`.`owner` = {frappe.db.escape(user)}")
	return "(" + " or ".join(parts) + ")"


def has_permission(doc, ptype=None, user=None, **kwargs):
	user = user or frappe.session.user
	if is_privileged(user):
		return True
	if doc.get("owner") == user or not (doc.get("salesperson") or doc.get("employee")):
		return True
	if doc.get("salesperson") and doc.salesperson in get_user_sales_persons(user):
		return True
	return bool(doc.get("employee") and doc.employee in get_user_employees(user))


@frappe.whitelist()
def get_default_sales_person(user=None):
	"""Returns the logged-in user's Sales Person and Employee (either may be None)."""
	user = user or frappe.session.user
	persons = get_user_sales_persons(user)
	employees = get_user_employees(user)
	return {
		"salesperson": persons[0] if persons else None,
		"employee": employees[0] if employees else None,
	}


# ----------------------------------------------------------------------
# Expense Claim integration
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_expense_claim(pjp_name):
	"""Create ONE Expense Claim from the Actual Cost table of a PJP."""
	pjp = frappe.get_doc("PJP Plan", pjp_name)
	pjp.check_permission("write")

	if pjp.docstatus != 1:
		frappe.throw(_("The PJP must be approved (submitted) first."))
	if pjp.expense_claim:
		frappe.throw(_("Expense Claim {0} is already linked to this PJP.").format(pjp.expense_claim))
	if not pjp.actual_costs:
		frappe.throw(_("Enter actual cost details before creating the Expense Claim."))
	if not pjp.employee:
		frappe.throw(
			_("No Employee is linked to Sales Person {0}. Set the Employee on the Sales Person first.").format(
				pjp.salesperson
			)
		)

	employee = frappe.db.get_value(
		"Employee", pjp.employee, ["company", "expense_approver"], as_dict=True
	)
	company = pjp.company or employee.company or frappe.defaults.get_user_default("Company")

	claim = frappe.new_doc("Expense Claim")
	claim.employee = pjp.employee
	claim.company = company
	claim.posting_date = nowdate()
	claim.expense_approver = employee.expense_approver
	claim.pjp_plan = pjp.name
	claim.remark = _("{0} | {1} | {2} | {3} to {4}").format(
		pjp.name, pjp.plan_type, get_purposes(pjp), pjp.from_date, pjp.to_date
	)

	company_defaults = frappe.get_cached_value(
		"Company", company, ["default_expense_claim_payable_account", "cost_center"], as_dict=True
	)
	if company_defaults:
		claim.payable_account = company_defaults.default_expense_claim_payable_account
		claim.cost_center = company_defaults.cost_center

	for row in pjp.actual_costs:
		if not frappe.db.exists("Expense Claim Type", row.cost_head):
			frappe.throw(_("Expense Claim Type {0} does not exist.").format(frappe.bold(row.cost_head)))
		claim.append(
			"expenses",
			{
				"expense_date": row.expense_date,
				"expense_type": row.cost_head,
				"description": f"{pjp.name}: " + _expense_description(row),
				"amount": row.actual_amount,
				"sanctioned_amount": row.actual_amount,
			},
		)

	# Adjust the pre-trip Employee Advance (once it is paid) against this claim.
	# Whatever the actuals exceed the advance by is the extra amount payable to the employee.
	summary = get_advance_summary(pjp)
	if pjp.employee_advance:
		adv = summary.advance
		if summary.allocate > 0:
			# Let HRMS build the advance rows: it fills Advance Account, Reference Type and
			# Reference Name (the Payment Entry / Journal Entry that paid the advance), which
			# the Expense Claim validation requires. One row is created per payment.
			from hrms.hr.doctype.expense_claim.expense_claim import get_expense_claim_advances

			get_expense_claim_advances(claim, frappe.get_doc("Employee Advance", adv.name))

			# HRMS allocates the whole unclaimed amount; cap it at the claim total so that
			# "total advance" never exceeds the sanctioned amount.
			remaining = summary.claim_total
			rows = []
			for row in claim.get("advances"):
				row.allocated_amount = min(flt(row.allocated_amount), remaining)
				remaining -= flt(row.allocated_amount)
				if flt(row.allocated_amount) > 0:
					rows.append(row)
			claim.set("advances", rows)
			if not rows:
				summary.allocate = 0
				summary.excess_to_return = 0
				summary.reason = _("no submitted payment found against the advance")

		if summary.allocate <= 0 or not claim.get("advances"):
			# Alert 1: advance exists but cannot be adjusted (draft / not paid yet / already used)
			frappe.msgprint(
				_(
					"Employee Advance {0} is not adjusted in this claim ({1}). "
					"Submit and pay the advance, then use 'Get Advances Paid' in the Expense Claim before submitting it."
				).format(pjp.employee_advance, summary.reason),
				title=_("Advance not adjusted"),
				indicator="orange",
			)

	claim.insert(ignore_permissions=True)

	# Alert 2: advance is more than the actual expense -> the employee must return the balance
	if summary.excess_to_return > 0:
		msg = _(
			"Advance {0} paid is more than the actual expense. Employee must return {1}. "
			"Open Employee Advance {0} and use Return (Payment Entry)."
		).format(
			pjp.employee_advance,
			frappe.format_value(summary.excess_to_return, {"fieldtype": "Currency"}),
		)
		frappe.msgprint(msg, title=_("Balance advance to be returned"), indicator="orange")
		pjp.add_comment("Info", msg)

	# Totals were already stored by validate; closing the PJP does not edit actuals.
	frappe.db.set_value(
		"PJP Plan", pjp.name, {"expense_claim": claim.name, "status": "Closed"}, update_modified=True
	)
	pjp.add_comment("Info", _("Expense Claim {0} created.").format(claim.name))
	return claim.name


def _expense_description(row):
	route = f"{row.from_place or ''} to {row.to_place or ''}".strip() if (row.from_place or row.to_place) else None
	bits = [row.travel_mode, route, row.description]
	return " | ".join(b for b in bits if b) or row.cost_head


@frappe.whitelist()
def get_settlement(pjp_name):
	"""Live money status of the Employee Advance and Expense Claim linked to a PJP.

	Shown on the PJP form so that the user sees amounts and payment / return status, not only the IDs.
	Nothing is stored on the PJP: the numbers are read from the linked documents every time.
	"""
	pjp = frappe.get_doc("PJP Plan", pjp_name)
	pjp.check_permission("read")
	return build_settlement(pjp)


def build_settlement(pjp):
	currency = frappe.get_cached_value("Company", pjp.company, "default_currency") if pjp.company else None
	out = frappe._dict(currency=currency, advance=None, claim=None)

	claim = None
	if pjp.expense_claim:
		claim = frappe.db.get_value(
			"Expense Claim",
			pjp.expense_claim,
			[
				"name", "docstatus", "status", "approval_status", "total_claimed_amount",
				"total_sanctioned_amount", "total_advance_amount", "grand_total", "total_amount_reimbursed",
			],
			as_dict=True,
		)
		if claim:
			# What the company has to pay the employee after the advance is adjusted
			claim.net_payable = flt(claim.grand_total)
			claim.paid = flt(claim.total_amount_reimbursed)
			claim.balance_due = max(claim.net_payable - claim.paid, 0) if claim.docstatus == 1 else 0

	adv = None
	if pjp.employee_advance:
		adv = frappe.db.get_value(
			"Employee Advance",
			pjp.employee_advance,
			["name", "docstatus", "status", "advance_amount", "paid_amount", "claimed_amount", "return_amount"],
			as_dict=True,
		)
		if adv:
			paid, claimed, returned = flt(adv.paid_amount), flt(adv.claimed_amount), flt(adv.return_amount)
			# A draft Expense Claim has the advance allocated, but the advance shows it as claimed only
			# after the claim is submitted
			in_draft_claim = 0
			if claim and claim.docstatus == 0:
				in_draft_claim = sum(
					flt(r.allocated_amount)
					for r in frappe.get_all(
						"Expense Claim Advance",
						filters={"parent": claim.name, "parenttype": "Expense Claim", "employee_advance": adv.name},
						fields=["allocated_amount"],
					)
				)
			adv.adjusted = claimed + in_draft_claim
			adv.returned = returned
			# Extra advance the employee still has to give back (known only once the claim exists)
			adv.to_return = max(paid - claimed - returned - in_draft_claim, 0) if (claim and paid) else 0

	out.advance = adv
	out.claim = claim
	return out


def get_advance_summary(pjp):
	"""How the linked Employee Advance (if any) settles against this PJP's actual cost.

	claim_total      = all actual cost rows (full amount is claimed)
	allocate         = part of the advance adjusted inside the claim = min(unclaimed advance, claim_total)
	net_payable      = claim_total - allocate  (extra amount company pays the employee)
	excess_to_return = advance still unclaimed after adjustment (employee returns it to the company)
	"""
	claim_total = sum(flt(r.actual_amount) for r in pjp.actual_costs)
	out = frappe._dict(
		advance=None,
		claim_total=claim_total,
		unclaimed=0,
		allocate=0,
		net_payable=claim_total,
		excess_to_return=0,
		reason="",
	)
	if not pjp.employee_advance:
		return out

	adv = frappe.db.get_value(
		"Employee Advance",
		pjp.employee_advance,
		["name", "docstatus", "posting_date", "paid_amount", "claimed_amount", "return_amount", "advance_account"],
		as_dict=True,
	)
	out.advance = adv
	if not adv:
		out.reason = _("advance not found")
		return out
	if adv.docstatus != 1:
		out.reason = _("advance is not submitted")
		return out

	out.unclaimed = flt(adv.paid_amount) - flt(adv.claimed_amount) - flt(adv.return_amount)
	if out.unclaimed <= 0:
		out.reason = _("advance is not paid yet or already fully used")
		return out

	out.allocate = min(out.unclaimed, claim_total)
	out.net_payable = claim_total - out.allocate
	out.excess_to_return = max(out.unclaimed - claim_total, 0)
	return out


@frappe.whitelist()
def get_claim_preview(pjp_name):
	"""Numbers shown in the 'Create Expense Claim' confirmation."""
	pjp = frappe.get_doc("PJP Plan", pjp_name)
	pjp.check_permission("read")
	s = get_advance_summary(pjp)
	return {
		"has_advance": bool(pjp.employee_advance),
		"employee_advance": pjp.employee_advance,
		"claim_total": s.claim_total,
		"advance_adjusted": s.allocate,
		"net_payable": s.net_payable,
		"excess_to_return": s.excess_to_return,
		"reason": s.reason,
	}


def release_pjp(doc, method=None):
	"""Expense Claim cancelled / deleted: unlink and reopen the PJP (status back to Visited)."""
	pjp = doc.get("pjp_plan")
	if not pjp or not frappe.db.exists("PJP Plan", pjp):
		return
	doc.ignore_linked_doctypes = ("PJP Plan",)
	if frappe.db.get_value("PJP Plan", pjp, "expense_claim") == doc.name:
		frappe.db.set_value(
			"PJP Plan", pjp, {"expense_claim": None, "status": "Visited"}, update_modified=True
		)


@frappe.whitelist()
def make_employee_advance(pjp_name, advance_amount):
	"""Create a draft Employee Advance (before the tour) against an approved PJP."""
	pjp = frappe.get_doc("PJP Plan", pjp_name)
	pjp.check_permission("write")
	amount = flt(advance_amount)

	if pjp.claim_mode == CLAIM_ONLY:
		frappe.throw(_("A new advance cannot be requested for an Ad-Hoc Visit PJP."))
	if pjp.docstatus != 1 or pjp.status != "Planned":
		frappe.throw(_("An advance can be requested only after approval and before actual details are entered."))
	if pjp.employee_advance:
		frappe.throw(_("Employee Advance {0} is already linked to this PJP.").format(pjp.employee_advance))
	if not pjp.employee:
		frappe.throw(_("Select the Employee on the PJP first."))
	if amount <= 0:
		frappe.throw(_("Advance Amount must be greater than zero."))
	if amount > flt(pjp.total_estimated_cost):
		frappe.throw(
			_("Advance Amount cannot exceed the Total Estimated Cost ({0}).").format(
				frappe.format_value(pjp.total_estimated_cost, {"fieldtype": "Currency"})
			)
		)

	company = pjp.company
	adv = frappe.new_doc("Employee Advance")
	adv.employee = pjp.employee
	adv.company = company
	adv.posting_date = nowdate()
	adv.purpose = _("{0} | {1} | {2} to {3}").format(pjp.name, get_purposes(pjp), pjp.from_date, pjp.to_date)
	adv.advance_amount = amount
	adv.pjp_plan = pjp.name

	defaults = frappe.db.get_value(
		"Company", company, ["default_currency", "default_employee_advance_account"], as_dict=True
	)
	if defaults:
		adv.currency = defaults.default_currency
		adv.exchange_rate = 1
		adv.advance_account = defaults.default_employee_advance_account

	adv.insert(ignore_permissions=True)
	frappe.db.set_value("PJP Plan", pjp.name, "employee_advance", adv.name, update_modified=True)
	pjp.add_comment("Info", _("Employee Advance {0} created for {1}.").format(adv.name, amount))
	return adv.name


def release_advance(doc, method=None):
	"""Employee Advance cancelled / deleted: unlink it from the PJP."""
	pjp = doc.get("pjp_plan")
	if not pjp or not frappe.db.exists("PJP Plan", pjp):
		return
	doc.ignore_linked_doctypes = ("PJP Plan",)
	if frappe.db.get_value("PJP Plan", pjp, "employee_advance") == doc.name:
		frappe.db.set_value("PJP Plan", pjp, "employee_advance", None, update_modified=True)
