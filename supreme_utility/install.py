# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Install / migrate setup. Everything here is idempotent: running it twice changes nothing."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

COST_HEADS = ["Travel", "Food", "Stay", "Parking/Toll", "Gifts", "Other"]

# Timesheet Activity Types used by the Sales Calendar hour view
ACTIVITY_TYPES = [
	"Sales Call / Follow-up",
	"Customer Visit",
	"Documentation",
	"Report / Analysis",
	"Other Work",
]

PJP_LINK_FIELD = {
	"fieldname": "pjp_plan",
	"fieldtype": "Link",
	"label": "PJP Plan",
	"options": "PJP Plan",
	"insert_after": "company",
	"read_only": 1,
	"no_copy": 1,
	"print_hide": 1,
	"module": "Supreme Utility",
}

CUSTOM_FIELDS = {
	"Expense Claim": [PJP_LINK_FIELD],
	"Employee Advance": [PJP_LINK_FIELD],
}


def after_install():
	setup()


def after_migrate():
	setup()


def setup():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	create_expense_claim_types()
	create_activity_types()
	frappe.db.commit()


def create_expense_claim_types():
	"""The cost heads of a PJP are Expense Claim Types (HRMS)."""
	if not frappe.db.exists("DocType", "Expense Claim Type"):
		return
	for head in COST_HEADS:
		if not frappe.db.exists("Expense Claim Type", head):
			frappe.get_doc(
				{
					"doctype": "Expense Claim Type",
					"expense_type": head,
					"description": f"PJP cost head: {head}",
				}
			).insert(ignore_permissions=True)


def create_activity_types():
	"""Timesheet Activity Types for the Sales Calendar (colours are decided by the type name)."""
	if not frappe.db.exists("DocType", "Activity Type"):
		return
	for name in ACTIVITY_TYPES:
		if not frappe.db.exists("Activity Type", name):
			frappe.get_doc({"doctype": "Activity Type", "activity_type": name}).insert(ignore_permissions=True)
