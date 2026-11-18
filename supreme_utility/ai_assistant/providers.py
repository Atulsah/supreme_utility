"""AI provider adapters.

To add a new AI provider:
  1. Create a new class based on BaseProvider and implement run()
  2. Register it in the PROVIDERS dict
  3. Add an option to the 'provider' Select of 'AI Provider Credential'
(No code needed for OpenAI-compatible APIs: use 'OpenAI Compatible' + Base URL.)
"""
import requests

LIMIT_MSG = "Could not produce an answer. Please try a simpler question."


class ProviderError(Exception):
	pass


class BusyError(ProviderError):
	"""Server busy / overloaded / rate limited / not reachable: worth trying another model or provider."""


RETRY_CODES = (429, 500, 502, 503, 504)


def _post(url, headers, body, retries=1):
	"""Fails fast: one quick retry after 2s, then BusyError so the next model / provider is tried."""
	import time

	last = None
	for attempt in range(retries + 1):
		try:
			r = requests.post(url, headers=headers, json=body, timeout=(8, 40))
		except requests.exceptions.RequestException as e:
			last = f"network: {str(e)[:200]}"
		else:
			if r.ok:
				return r.json()
			last = f"{r.status_code}: {r.text[:300]}"
			if r.status_code not in RETRY_CODES:
				raise ProviderError(last)
		if attempt < retries:
			time.sleep(2)
	raise BusyError(last)


class BaseProvider:
	def __init__(self, model, api_key, base_url=None):
		self.model = model
		self.api_key = api_key
		self.base_url = (base_url or "").rstrip("/")

	def run(self, question, system, tools, run_tool, max_steps=5):
		"""tools: neutral list; run_tool(name, args) -> dict. Return the final text."""
		raise NotImplementedError


class ClaudeProvider(BaseProvider):
	def run(self, question, system, tools, run_tool, max_steps=5):
		url = (self.base_url or "https://api.anthropic.com") + "/v1/messages"
		headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
		t = [{"name": x["name"], "description": x["description"], "input_schema": x["parameters"]} for x in tools]
		msgs = [{"role": "user", "content": question}]
		for _ in range(max_steps):
			d = _post(url, headers, {
				"model": self.model, "max_tokens": 2500,
				"system": system, "tools": t, "messages": msgs,
			})
			if d.get("stop_reason") != "tool_use":
				return "".join(b.get("text", "") for b in d["content"] if b["type"] == "text")
			msgs.append({"role": "assistant", "content": d["content"]})
			msgs.append({"role": "user", "content": [
				{"type": "tool_result", "tool_use_id": b["id"], "content": str(run_tool(b["name"], b["input"]))}
				for b in d["content"] if b["type"] == "tool_use"
			]})
		return LIMIT_MSG


class OpenAIProvider(BaseProvider):
	"""ChatGPT + any OpenAI-compatible API (Groq, DeepSeek, local...)."""

	def run(self, question, system, tools, run_tool, max_steps=5):
		import json
		base = self.base_url or "https://api.openai.com/v1"
		headers = {"Authorization": f"Bearer {self.api_key}"}
		t = [{"type": "function", "function": x} for x in tools]
		msgs = [{"role": "system", "content": system}, {"role": "user", "content": question}]
		extra = {}
		for _ in range(max_steps):
			body = {"model": self.model, "messages": msgs, "tools": t, **extra}
			try:
				d = _post(base + "/chat/completions", headers, body)
			except ProviderError as e:
				# Reasoning models: reasoning_effort must be turned off when using function tools
				if "reasoning_effort" in str(e) and "reasoning_effort" not in extra:
					extra["reasoning_effort"] = "none"
					d = _post(base + "/chat/completions", headers, {**body, **extra})
				else:
					raise
			m = d["choices"][0]["message"]
			calls = m.get("tool_calls")
			if not calls:
				return m.get("content") or ""
			msgs.append(m)
			for c in calls:
				args = json.loads(c["function"].get("arguments") or "{}")
				msgs.append({
					"role": "tool", "tool_call_id": c["id"],
					"content": json.dumps(run_tool(c["function"]["name"], args)),
				})
		return LIMIT_MSG


class GeminiProvider(BaseProvider):
	def run(self, question, system, tools, run_tool, max_steps=5):
		base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
		url = f"{base}/models/{self.model}:generateContent"
		headers = {"x-goog-api-key": self.api_key}
		contents = [{"role": "user", "parts": [{"text": question}]}]
		# Less "thinking" = much faster answers for simple data questions
		m = self.model.lower()
		think = {}
		if "2.5-flash" in m:
			think = {"thinkingConfig": {"thinkingBudget": 0}}
		elif m.startswith("gemini-3"):
			think = {"thinkingConfig": {"thinkingLevel": "low"}}
		for _ in range(max_steps):
			body = {
				"systemInstruction": {"parts": [{"text": system}]},
				"contents": contents,
				"tools": [{"functionDeclarations": tools}],
			}
			if think:
				body["generationConfig"] = think
			try:
				d = _post(url, headers, body)
			except BusyError:
				raise
			except ProviderError as e:
				if not think or "thinking" not in str(e).lower():
					raise
				think = {}  # this model does not accept the setting: retry without it
				body.pop("generationConfig", None)
				d = _post(url, headers, body)
			if not d.get("candidates"):
				raise ProviderError("Gemini returned no answer (it may have been blocked)")
			content = d["candidates"][0].get("content", {})
			parts = content.get("parts", [])
			calls = [p["functionCall"] for p in parts if "functionCall" in p]
			if not calls:
				return "".join(p.get("text", "") for p in parts)
			contents.append(content)  # keep as-is (thought signatures are preserved)
			contents.append({"role": "user", "parts": [
				{"functionResponse": {"name": c["name"], "response": {"result": run_tool(c["name"], c.get("args", {}))}}}
				for c in calls
			]})
		return LIMIT_MSG


PROVIDERS = {
	"Claude": ClaudeProvider,
	"ChatGPT": OpenAIProvider,
	"Gemini": GeminiProvider,
	"OpenAI Compatible": OpenAIProvider,
}
