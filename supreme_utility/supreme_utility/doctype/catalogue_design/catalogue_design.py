# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cstr

from supreme_utility.messages import _


def design_name(quality, design_no):
	"""Record name: 'Tarang-149' (or just '149' when there is no Quality)."""
	quality, design_no = cstr(quality).strip(), cstr(design_no).strip()
	return f"{quality}-{design_no}" if quality else design_no


class CatalogueDesign(Document):
	def autoname(self):
		self.name = design_name(self.quality, self.design_no)

	def validate(self):
		self.design_no = cstr(self.design_no).strip()
		self.quality = cstr(self.quality).strip() or None
		self.colour = cstr(self.colour).strip() or None
		dup = frappe.db.get_value(
			"Catalogue Design",
			{
				"design_no": self.design_no,
				"quality": self.quality or ("is", "not set"),
				"name": ("!=", self.name),
			},
			"name",
		)
		if dup:
			frappe.throw(_("Design No {0} already exists: {1}").format(self.design_no, dup))
