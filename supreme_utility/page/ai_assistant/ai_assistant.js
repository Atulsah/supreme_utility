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
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="ai-input-wrap" style="flex:1"></div>
        <button class="btn btn-default ai-mic" title="Bol kar puchiye">🎤</button>
        <button class="btn btn-primary ai-send">Send</button>
      </div>
      <label style="margin-top:8px"><input type="checkbox" class="ai-speak"> Jawab bol kar sunao</label>
    </div>`);

  const $b = $(page.body);
  const $log = $b.find(".ai-log");
  $b.find(".ai-input-wrap").append("<style>.ai-input-wrap .form-group{margin-bottom:0}</style>");
  const qctl = frappe.ui.form.make_control({
    parent: $b.find(".ai-input-wrap"),
    df: { fieldtype: "Data", fieldname: "ai_question", placeholder: "Jaise: Customer ABC ka last bill kya hai?" },
    render_input: true,
  });
  qctl.refresh();
  const $input = {
    val: (v) => (v === undefined ? (qctl.get_value() || "") : qctl.set_value(v)),
    prop: () => $input,
    focus: () => qctl.$input && qctl.$input.focus(),
    on: (ev, fn) => qctl.$input.on(ev === "keypress" ? "keydown" : ev, fn),
  };
  const $sel = $b.find(".ai-provider");

  // enabled AI providers dropdown me
  frappe.call({
    method: "supreme_utility.ai_assistant.api.get_providers",
    callback: (r) => {
      const list = r.message || [];
      if (!list.length) {
        $sel.append(new Option("Koi AI configured nahi", ""));
        $log.append("<div class='text-muted'>Pehle 'AI Assistant Settings' me koi AI provider add karo.</div>");
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
    $m.append($("<b>").text(who + ": ")).append(document.createTextNode(text));
    $log.append($m);
    $log.scrollTop($log[0].scrollHeight);
  };

  const speak = (text) => {
    if (!$b.find(".ai-speak").is(":checked") || !window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "hi-IN";
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  };

  const send = () => {
    const q = $input.val().trim();
    if (!q) return;
    add("Aap", q);
    $input.val("");
    const $wait = $("<div class='text-muted'>soch raha hoon...</div>");
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
  $input.on("keypress", (e) => e.which === 13 && (e.preventDefault(), send()));

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const $mic = $b.find(".ai-mic");
  if (!SR) {
    $mic.prop("disabled", true).attr("title", "Is browser me voice support nahi hai");
  } else {
    const rec = new SR();
    rec.lang = "hi-IN"; // English zyada ho to "en-IN"
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
