import frappe
from frappe.model.document import Document


class AIAssistantSettings(Document):
	def validate(self):
		for r in self.providers:
			if r.is_default and not r.enabled:
				r.is_default = 0

		enabled = [r for r in self.providers if r.enabled]
		defaults = [r for r in enabled if r.is_default]
		if len(defaults) > 1:
			frappe.throw("Only one provider can be the Default")
		if enabled and not defaults:
			enabled[0].is_default = 1
