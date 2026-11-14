frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "AI Assistant",
    single_column: true,
  });

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

  const add = (who, text) => {
    const $m = $("<div>").css({ margin: "8px 0", whiteSpace: "pre-wrap" });
    $m.append($("<b>").text(who + ": "));
    // Turn URLs in the answer into clickable "open" links (text is added safely, never as HTML)
    text.split(/(https?:\/\/[^\s<>"']+)/g).forEach((part, i) => {
      if (i % 2 === 0) return $m.append(document.createTextNode(part));
      const m = part.match(/^(.*?)([.,;:!?)\]]*)$/);
      let href = m[1];
      try {
        const u = new URL(m[1]);
        href = window.location.origin + u.pathname + u.search + u.hash; // always open on the current site
      } catch (e) {}
      $m.append($("<a>").attr({ href, target: "_blank", rel: "noopener" }).text("open ↗"));
      $m.append(document.createTextNode(m[2]));
    });
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
    add("You", q);
    $input.val("");
    const $wait = $("<div class='text-muted'>thinking...</div>");
    $log.append($wait);
    frappe.call({
      method: "supreme_utility.ai_assistant.api.ask",
      args: { question: q, provider_id: $sel.val() },
      callback: (r) => {
        $wait.remove();
        const a = r.message.answer;
        add("AI", a);
        speak(a);
      },
      error: () => $wait.remove(),
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
