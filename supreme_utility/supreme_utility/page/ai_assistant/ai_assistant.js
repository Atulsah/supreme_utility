frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "AI Assistant",
    // shown under the title: if you do not see "links v2", the browser is still using the old script
    // (run bench clear-cache and press Ctrl+Shift+R)
    single_column: true,
  });

  page.set_indicator("links v3", "green");

  $(page.body).html(`
    <div style="max-width:720px;margin:auto">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
        <label style="margin:0">AI:</label>
        <select class="form-control ai-provider" style="max-width:300px"></select>
      </div>
      <div class="ai-log" style="min-height:300px;max-height:60vh;overflow:auto;padding:12px;
           border:1px solid var(--border-color);border-radius:8px;margin-bottom:12px"></div>
      <div style="display:flex;gap:8px;align-items:center">
        <input type="text" class="form-control ai-input" autocomplete="off" spellcheck="false" style="flex:1" placeholder="e.g. What is the last bill of customer ABC?">
        <button class="btn btn-default ai-mic" title="Ask by voice">🎤</button>
        <button class="btn btn-primary ai-send">Send</button>
      </div>
      <label style="margin-top:8px"><input type="checkbox" class="ai-speak"> Read answers aloud</label>
    </div>`);

  const $b = $(page.body);
  const $log = $b.find(".ai-log");
  const el = $b.find(".ai-input")[0];
  const $input = {
    val: (v) => { if (v === undefined) return el.value; el.value = v; return $input; },
    prop: () => $input,
    focus: () => el.focus(),
  };

  // Typing protection: (1) other page scripts never see our keystrokes,
  // (2) if a script still blocks a key, we insert it ourselves.
  ["keydown", "keypress", "keyup"].forEach((t) => el.addEventListener(t, (e) => e.stopPropagation()));
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); send(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    const key = e.key;
    setTimeout(() => {
      if (!e.defaultPrevented) return; // browser typed it normally
      const a = el.selectionStart, b = el.selectionEnd;
      if (key.length === 1) el.setRangeText(key, a, b, "end");
      else if (key === "Backspace") {
        if (a !== b) el.setRangeText("", a, b, "end");
        else if (a > 0) el.setRangeText("", a - 1, a, "end");
      }
    }, 0);
  });
  const $sel = $b.find(".ai-provider");

  // enabled AI providers dropdown me
  frappe.call({
    method: "supreme_utility.ai_assistant.api.get_providers",
    callback: (r) => {
      const list = r.message || [];
      if (!list.length) {
        $sel.append(new Option("No AI configured", ""));
        $log.append("<div class='text-muted'>First add an AI provider in 'AI Assistant Settings'.</div>");
        return;
      }
      list.forEach((p) => $sel.append(new Option(p.label, p.id)));
      const saved = localStorage.getItem("su_ai_provider");
      const def = list.find((p) => p.is_default);
      $sel.val(list.find((p) => p.id === saved) ? saved : (def || list[0]).id);
    },
  });
  setTimeout(() => $input.prop("disabled", false).prop("readonly", false).focus(), 300);
  $sel.on("change", () => localStorage.setItem("su_ai_provider", $sel.val()));

  const esc = (t) => frappe.utils.escape_html(String(t));
  const reEsc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Turns document IDs / names returned by the tools, and /app/... URLs, into links
  const linkify = (text, links) => {
    links = links || {};
    const names = Object.keys(links).filter((n) => n && n.length > 2).sort((a, b) => b.length - a.length);
    const parts = ["(?:https?://[^\\s]+|/app/[^\\s]+)"];
    if (names.length) parts.push("(?<![\\w/-])(?:" + names.map(reEsc).join("|") + ")(?![\\w-])");
    const re = new RegExp(parts.join("|"), "g");
    let html = "", last = 0, m;
    while ((m = re.exec(text))) {
      const isUrl = /^(\/app\/|https?:)/.test(m[0]);
      const tok = isUrl ? m[0].replace(/[.,;:)]+$/, "") : m[0];
      html += esc(text.slice(last, m.index));
      if (isUrl) {
        const label = tok.startsWith("/app/") ? "View in list \u2197" : tok;
        html += `<a href="${esc(tok)}" target="_blank" rel="noopener">${esc(label)}</a>`;
      } else {
        html += `<a href="${esc(links[tok])}" target="_blank" rel="noopener">${esc(tok)}</a>`;
      }
      last = m.index + tok.length;
      re.lastIndex = last;
    }
    return html + esc(text.slice(last));
  };

  const add = (who, text, links) => {
    const $m = $("<div>").css({ margin: "8px 0", whiteSpace: "pre-wrap" });
    $m.append($("<b>").text(who + ": ")).append($("<span>").html(linkify(text, links)));
    $log.append($m);
    $log.scrollTop($log[0].scrollHeight);
  };

  const speak = (text) => {
    if (!$b.find(".ai-speak").is(":checked") || !window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-IN";
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  };

  const send = () => {
    const q = $input.val().trim();
    if (!q) return;
    add("You", q, {});
    $input.val("");
    const $wait = $("<div class='text-muted'>thinking... <span>0</span>s</div>");
    $log.append($wait);
    $log.scrollTop($log[0].scrollHeight);
    const t0 = Date.now();
    const tick = setInterval(() => $wait.find("span").text(Math.round((Date.now() - t0) / 1000)), 1000);
    $b.find(".ai-send").prop("disabled", true);
    const done = () => { clearInterval(tick); $wait.remove(); $b.find(".ai-send").prop("disabled", false); };
    frappe.call({
      method: "supreme_utility.ai_assistant.api.ask",
      args: { question: q, provider_id: $sel.val() },
      callback: (r) => {
        done();
        const a = r.message.answer;
        add("AI", a, r.message.links);
        if (r.message.note) {
          $log.append($("<div class='text-muted small'>").text("(" + r.message.note + ")"));
          $log.scrollTop($log[0].scrollHeight);
        }
        speak(a.replace(/https?:\/\/\S+|\/app\/\S+/g, ""));
      },
      error: () => done(),
    });
  };

  $b.find(".ai-send").on("click", send);

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const $mic = $b.find(".ai-mic");
  if (!SR) {
    $mic.prop("disabled", true).attr("title", "Voice input is not supported in this browser");
  } else {
    const rec = new SR();
    rec.lang = "en-IN"; // use "hi-IN" for Hindi speech
    rec.interimResults = false;
    rec.onresult = (e) => {
      $input.val(e.results[0][0].transcript);
      send();
    };
    rec.onend = () => $mic.removeClass("btn-danger");
    $mic.on("click", () => {
      $mic.addClass("btn-danger");
      rec.start();
    });
  }
};
