# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CatalogueSettings(Document):
	def validate(self):
		if self.quality_from != "Item Attribute":
			self.quality_attribute = None
