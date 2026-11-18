import frappe
from frappe.utils import nowdate
from frappe.utils.password import get_decrypted_password

import time

from supreme_utility.ai_assistant.providers import PROVIDERS, BusyError, ProviderError
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
	chosen = (
		next((r for r in rows if r.name == provider_id), None)
		or next((r for r in rows if r.is_default), None)
		or rows[0]
	)
	# Try order: chosen provider (its model, then its fallback models), then the other enabled providers
	others = sorted((r for r in rows if r.name != chosen.name), key=lambda r: not r.is_default)
	attempts = []
	for r in [chosen] + others:
		models = [(r.model or "").strip()] + [
			m.strip() for m in (r.get("fallback_models") or "").replace("\n", ",").split(",") if m.strip()
		]
		for m in dict.fromkeys(models):
			attempts.append((r, m))

	system = (
		"You are an assistant for ERPNext that answers Sales, Purchase, Stock, Accounts and HR (HRMS) questions. "
		"Use only the provided tools to get data and never guess. "
		"Answer from whatever angle the user asks: date-wise, party-wise, item-wise, status-wise or a mix. "
		"For any list / last N / detail question about invoices, orders, delivery notes, quotations, purchases "
		"or payments use find_documents; for totals grouped by party, item, item group, territory, day or month "
		"use analyze_transactions. A customer, supplier or item is optional: if the user did not name one, "
		"search across all of them and do NOT ask for it. Ask only when a tool says multiple matches were found. "
		"Default to Sales Invoice when the user just says bill or invoice. "
		"Always write document IDs (like SINV-2026-00012), customer, supplier and item names exactly as the tool "
		"returned them, without brackets or quotes, because the app turns them into clickable links. "
		"If a tool returns open_list_url or documents_url, add it on its own line as 'Open list: <url>'. "
		"For each document show: ID, date, party, amount, status, and its item lines (item, qty, rate) when asked "
		"for details. "
		"Share private information such as salary only when a tool returns it. "
		"Always reply in English with short, clear answers. Show amounts with their currency. "
		"Do not use Markdown (no **, *, #); write simple plain lines. "
		f"Today's date is {nowdate()}; convert phrases like 'this month' or 'last week' into date ranges."
	)
	started = time.monotonic()
	failed = []
	for row, model in attempts:
		if time.monotonic() - started > 70:
			break
		cls = PROVIDERS.get(row.provider)
		key = get_decrypted_password("AI Provider Credential", row.name, "api_key", raise_exception=False)
		if not cls or not key:
			continue
		frappe.local.ai_links = {}
		try:
			answer = cls(model, key.strip(), (row.base_url or "").strip()).run(question, system, TOOLS, run_tool)
		except BusyError as e:
			failed.append(f"{row.provider} ({model}) busy")
			frappe.log_error(f"{row.provider} {model}: {e}", "AI Assistant provider busy")
			continue
		except Exception as e:
			failed.append(f"{row.provider} ({model}) error: {str(e)[:150]}")
			frappe.log_error(frappe.get_traceback(), "AI Assistant provider error")
			continue
		note = f"{', '.join(failed)}; answered by {row.provider} ({model})" if failed else ""
		return {"answer": answer, "links": frappe.local.ai_links, "note": note,
			"seconds": round(time.monotonic() - started, 1)}

	if not failed:
		frappe.throw("API key is not set for any enabled AI provider")
	return {"answer": "The AI could not answer right now (" + "; ".join(failed) + "). "
		"Please try again in a minute, or add another provider / fallback model in AI Assistant Settings.",
		"links": {}, "note": ""}
