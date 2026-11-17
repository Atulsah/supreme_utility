# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint

from supreme_utility.messages import _


class SalesCalendarSettings(Document):
	def validate(self):
		start, end = cint(self.work_start), cint(self.work_end)
		if not (0 <= start <= 23):
			frappe.throw(_("Work Start Hour must be between 0 and 23."))
		if not (1 <= end <= 24):
			frappe.throw(_("Work End Hour must be between 1 and 24."))
		if end <= start:
			frappe.throw(_("Work End Hour must be after Work Start Hour."))
		if not (3 <= end - start <= 12):
			frappe.throw(_("Working hours must cover between 3 and 12 hours."))
