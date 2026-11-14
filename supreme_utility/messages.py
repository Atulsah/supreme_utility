# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Every message of this app is shown in English, whatever language the user has selected."""

import frappe


def _(message, context=None):
	"""Like frappe._(), but never translated."""
	return frappe._(message, lang="en", context=context)
