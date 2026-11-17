import frappe
from frappe.utils import nowdate
from frappe.utils.password import get_decrypted_password

from supreme_utility.ai_assistant.providers import PROVIDERS, ProviderError
from supreme_utility.ai_assistant.tools import TOOLS, run_tool


def _enabled_rows():
	settings = frappe.get_single("AI Assistant Settings")
	return [r for r in settings.providers if r.enabled]


@frappe.whitelist()
def get_providers():
	"""Enabled providers for the dropdown (the API key is never sent to the client)."""
	return [
		{"id": r.name, "label": f"{r.provider} ({r.model})", "is_default": r.is_default}
		for r in _enabled_rows()
	]


@frappe.whitelist()
def ask(question, provider_id=None):
	question = (question or "").strip()
	if not question:
		return {"answer": "Please type a question."}
	if len(question) > 500:
		frappe.throw("The question is too long")

	rows = _enabled_rows()
	if not rows:
		frappe.throw("No enabled AI provider found in 'AI Assistant Settings'")
	row = (
		next((r for r in rows if r.name == provider_id), None)
		or next((r for r in rows if r.is_default), None)
		or rows[0]
	)

	cls = PROVIDERS.get(row.provider)
	if not cls:
		frappe.throw(f"No adapter is available for provider '{row.provider}'")
	key = get_decrypted_password("AI Provider Credential", row.name, "api_key", raise_exception=False)
	if not key:
		frappe.throw(f"API key is not set for {row.provider}")

	system = (
		"You are an assistant for ERPNext that answers Sales and HR (HRMS) questions. "
		"Use only the provided tools to get data and never guess. "
		"Share private information such as salary only when a tool returns it. "
		"Always reply in English with short, clear answers. Show amounts with their currency. "
		"Do not use Markdown (no **, *, #); write simple plain lines. "
		f"Today's date is {nowdate()}; convert phrases like 'this month' or 'last week' into date ranges."
	)
	try:
		answer = cls((row.model or "").strip(), (key or "").strip(), (row.base_url or "").strip()).run(question, system, TOOLS, run_tool)
	except (ProviderError, Exception) as e:
		frappe.log_error(frappe.get_traceback(), "AI Assistant provider error")
		return {"answer": f"Error from {row.provider}: {str(e)[:700]}"}
	return {"answer": answer}
