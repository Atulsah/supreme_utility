// Sales Calendar: every Sales Person x every day.
// Red = Leave, Green = Tour, Blue = Event / working on something, Yellow = Idle, Grey = Holiday.
(function () {

frappe.pages["sales-calendar"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sales Calendar"),
		single_column: true,
	});
	try {
		wrapper.sales_calendar = new SalesCalendar(page, wrapper);
	} catch (e) {
		SalesCalendar.show_error(page, e);
	}
};

frappe.pages["sales-calendar"].on_page_show = function (wrapper) {
	if (wrapper.sales_calendar) wrapper.sales_calendar.refresh();
};

const SC_API = "supreme_utility.supreme_utility.page.sales_calendar.sales_calendar.get_schedule";
const SC_STATES = {
	leave: { label: __("On Leave"), code: "L" },
	tour: { label: __("On Tour"), code: "T" },
	work: { label: __("Working"), code: "W" },
	idle: { label: __("Idle"), code: "" },
	holiday: { label: __("Holiday / Off"), code: "H" },
	future: { label: __("Not planned yet"), code: "" },
};
const SC_COLORS = {
	call: "var(--sc-call)",
	visit: "var(--sc-vis)",
	doc: "var(--sc-doc)",
	report: "var(--sc-rep)",
	work: "var(--sc-wk)",
	auto: "#fbd9b0",
	idle: "var(--sc-idle)",
	off: "#dcdde3",
	future: "#f1f1f1",
	gap: "#f4f4f4",
};
const SC_LOG_API = "supreme_utility.supreme_utility.page.sales_calendar.sales_calendar.log_activity";
// hour slot colours (Timesheet Activity Types are created by install.py)
const SC_KINDS = {
	call: __("Sales call / Follow-up"),
	visit: __("Customer visit"),
	doc: __("Documentation"),
	report: __("Report / Analysis"),
	work: __("Other work"),
};
const SC_ACTIVITY_TYPES = [
	"Sales Call / Follow-up",
	"Customer Visit",
	"Documentation",
	"Report / Analysis",
	"Other Work",
];

class SalesCalendar {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.view = "Month";
		this.anchor = frappe.datetime.str_to_obj(frappe.datetime.get_today());
		this.selected = frappe.datetime.get_today();
		this.data = null;
		this.ready = false;
		this.make_controls();
		this.make_body();
		this.ready = true;
		this.refresh();
	}

	make_controls() {
		const p = this.page;
		this.group_field = p.add_field({
			fieldname: "sales_person_group",
			label: __("Sales Person / Group"),
			fieldtype: "Link",
			options: "Sales Person",
			change: () => this.ready && this.refresh(),
		});
		this.view_field = p.add_field({
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Month", "Week", "Fortnight", "Day"],
			default: "Month",
			change: () => {
				if (!this.ready) return;
				this.view = this.view_field.get_value() || "Month";
				this.refresh();
			},
		});
		this.go_field = p.add_field({
			fieldname: "go_to",
			label: __("Go to date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			change: () => {
				if (!this.ready) return;
				const v = this.go_field.get_value();
				if (!v) return;
				this.anchor = frappe.datetime.str_to_obj(v);
				this.selected = v;
				this.refresh();
			},
		});
		this.show_field = p.add_field({
			fieldname: "show",
			label: __("Show on selected day"),
			fieldtype: "Select",
			options: ["", "On Leave", "On Tour", "Working", "Idle", "Holiday / Off"],
			change: () => this.ready && this.render(),
		});
		this.search_field = p.add_field({
			fieldname: "search",
			label: __("Search"),
			fieldtype: "Data",
			change: () => this.ready && this.render(),
		});
		p.set_primary_action(__("Today"), () => {
			this.anchor = frappe.datetime.str_to_obj(frappe.datetime.get_today());
			this.selected = frappe.datetime.get_today();
			this.refresh();
		});
		p.add_inner_button(__("Day View"), () => this.open_day());
		p.add_inner_button(__("Month View"), () => this.set_view("Month"));
		p.add_inner_button(__("Previous"), () => this.shift(-1));
		p.add_inner_button(__("Next"), () => this.shift(1));
		p.add_menu_item(__("Refresh"), () => this.refresh());
		p.add_menu_item(__("Working hours setting"), () => frappe.set_route("Form", "Sales Calendar Settings"));
		p.add_menu_item(__("New PJP Plan"), () => frappe.new_doc("PJP Plan"));
		p.add_menu_item(__("Leave Applications"), () => frappe.set_route("List", "Leave Application"));
	}

	make_body() {
		this.$body = $(this.page.body);
		this.$body.html(`
			<style>${SalesCalendar.css()}</style>
			<div class="sc-wrap">
				<div class="sc-head"></div>
				<div class="sc-cards"></div>
				<div class="sc-legend"></div>
				<div class="sc-scroll"><table class="sc-table"></table></div>
				<div class="sc-empty text-muted" style="display:none"></div>
			</div>
			<div class="sc-tip" style="display:none"></div>`);
		this.$head = this.$body.find(".sc-head");
		this.$cards = this.$body.find(".sc-cards");
		this.$legend = this.$body.find(".sc-legend");
		this.$table = this.$body.find(".sc-table");
		this.$empty = this.$body.find(".sc-empty");
		this.$tip = this.$body.find(".sc-tip");
	}

	range() {
		const a = new Date(this.anchor.getTime());
		let start, end;
		if (this.view === "Day") {
			start = new Date(a.getFullYear(), a.getMonth(), a.getDate());
			end = start;
		} else if (this.view === "Week") {
			const dow = (a.getDay() + 6) % 7; // Monday first
			start = new Date(a.getFullYear(), a.getMonth(), a.getDate() - dow);
			end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
		} else if (this.view === "Fortnight") {
			start = new Date(a.getFullYear(), a.getMonth(), a.getDate());
			end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 13);
		} else {
			start = new Date(a.getFullYear(), a.getMonth(), 1);
			end = new Date(a.getFullYear(), a.getMonth() + 1, 0);
		}
		return [moment(start).format("YYYY-MM-DD"), moment(end).format("YYYY-MM-DD")];
	}

	shift(dir) {
		const a = this.anchor;
		if (this.view === "Day") this.anchor = new Date(a.getFullYear(), a.getMonth(), a.getDate() + dir);
		else if (this.view === "Week") this.anchor = new Date(a.getFullYear(), a.getMonth(), a.getDate() + 7 * dir);
		else if (this.view === "Fortnight")
			this.anchor = new Date(a.getFullYear(), a.getMonth(), a.getDate() + 14 * dir);
		else this.anchor = new Date(a.getFullYear(), a.getMonth() + dir, 1);
		this.refresh();
	}

	set_view(view, day) {
		if (day) {
			this.selected = day;
			this.anchor = frappe.datetime.str_to_obj(day);
		} else if (view === "Day") {
			this.anchor = frappe.datetime.str_to_obj(this.selected);
		}
		this.view = view;
		this.ready = false;
		this.view_field.set_value(view);
		this.ready = true;
		this.refresh();
	}

	open_day() {
		this.set_view("Day", this.selected);
	}

	refresh() {
		const [from_date, to_date] = this.range();
		if (this.go_field) {
			const shown = this.view === "Day" ? from_date : this.selected || from_date;
			if (this.go_field.get_value() !== shown) {
				this.ready = false;
				this.go_field.set_value(shown);
				this.ready = true;
			}
		}
		this.$head.html(`<div class="text-muted">${__("Loading...")}</div>`);
		frappe.call({
			method: SC_API,
			args: {
				from_date,
				to_date,
				sales_person_group: this.group_field.get_value() || null,
			},
			callback: (r) => {
				try {
					this.data = r.message;
					if (!this.data.days.includes(this.selected)) {
						this.selected = this.data.days.includes(this.data.today)
							? this.data.today
							: this.data.days[0];
					}
					this.render();
				} catch (e) {
					SalesCalendar.show_error(this.page, e);
				}
			},
			error: () => {
				SalesCalendar.show_error(
					this.page,
					__("The server returned an error. Open Error Log (or the bench terminal) for the traceback.")
				);
			},
		});
	}

	static show_error(page, e) {
		console.error(e);
		const esc = frappe.utils.escape_html;
		$(page.body).html(
			`<div style="padding:20px"><b class="text-danger">${__("Sales Calendar could not load")}</b>
			<pre style="white-space:pre-wrap;margin-top:8px">${esc(String((e && e.stack) || e))}</pre></div>`
		);
	}

	render() {
		const d = this.data;
		if (!d) return;
		const esc = frappe.utils.escape_html;
		const [from_date, to_date] = [d.from_date, d.to_date];
		const title =
			this.view === "Month"
				? moment(from_date).format("MMMM YYYY")
				: this.view === "Day"
				? moment(from_date).format("dddd, DD MMM YYYY")
				: `${frappe.datetime.str_to_user(from_date)} - ${frappe.datetime.str_to_user(to_date)}`;
		this.$head.html(`<div class="sc-title">${esc(title)}</div>
			<div class="text-muted small">${__("{0} sales persons", [d.rows.length])}. ${__(
			"Click a date to see who is where on that day. Click a coloured cell to open it."
		)}</div>`);

		// summary of the selected day
		const sum = d.summary[this.selected] || {};
		this.$cards.html(
			`<div class="sc-sel">${esc(moment(this.selected).format("dddd, DD MMM YYYY"))}
				${this.view === "Day" ? "" : `<a class="sc-open-day">${__("Open day view")}</a>`}</div>` +
				["leave", "tour", "work", "idle", "holiday"]
					.map(
						(k) => `<div class="sc-card sc-${k}" data-state="${k}">
					<div class="sc-num">${sum[k] || 0}</div><div class="sc-lbl">${esc(SC_STATES[k].label)}</div></div>`
					)
					.join("")
		);
		this.$cards.find(".sc-open-day").on("click", () => this.open_day());
		this.$cards.find(".sc-card").on("click", (e) => {
			const label = SC_STATES[$(e.currentTarget).data("state")].label;
			this.show_field.set_value(this.show_field.get_value() === label ? "" : label);
		});

		this.$legend.html(
			["leave", "tour", "work", "idle", "holiday"]
				.map((k) => `<span class="sc-key"><i class="sc-dot sc-${k}"></i>${esc(SC_STATES[k].label)}</span>`)
				.join("") +
				`<span class="sc-key"><i class="sc-dot sc-pending"></i>${__("Not approved yet")}</span>` +
				`<span class="sc-key sc-sep">${__("Hours ({0}:00 to {1}:00):", [d.work_start, d.work_end])}</span>` +
				Object.keys(SC_KINDS)
					.map((k) => `<span class="sc-key"><i class="sc-dot sc-k-${k}"></i>${esc(SC_KINDS[k])}</span>`)
					.join("") +
				`<span class="sc-key"><i class="sc-dot sc-k-auto"></i>${__("Suggested (call / quotation, not in Timesheet)")}</span>` +
				`<span class="sc-key"><i class="sc-dot sc-k-idle"></i>${__("Idle hour")}</span>` +
				`<span class="sc-key"><i class="sc-dot sc-k-off"></i>${__("Not in office")}</span>`
		);

		// rows
		const show = this.show_field.get_value();
		const q = (this.search_field.get_value() || "").toLowerCase();
		const want = Object.keys(SC_STATES).find((k) => SC_STATES[k].label === show);
		const rows = d.rows.filter((r) => {
			if (q && !(r.label + " " + (r.employee || "") + " " + (r.group || "")).toLowerCase().includes(q))
				return false;
			if (want && r.cells[this.selected].s !== want) return false;
			return true;
		});

		this.$table.toggleClass("sc-wide", this.view === "Week");
		this.$table.toggleClass("sc-day-view", this.view === "Day");
		let html = `<thead><tr><th class="sc-name">${__("Sales Person")}</th>`;
		if (this.view === "Day") {
			const labels = [];
			for (let h = d.work_start; h < d.work_end; h++) labels.push(`<span>${h}:00</span>`);
			html += `<th class="sc-dayhead"><div class="sc-ruler" style="grid-template-columns:repeat(${labels.length},1fr)">${labels.join("")}</div></th>`;
		} else {
			d.days.forEach((day) => {
				const m = moment(day);
				const cls = [
					day === d.today ? "sc-today" : "",
					day === this.selected ? "sc-selected" : "",
					m.day() === 0 ? "sc-sun" : "",
				].join(" ");
				html += `<th class="sc-day ${cls}" data-day="${day}"><div>${m.format("DD")}</div><div class="sc-dow">${m.format(
					"dd"
				)}</div></th>`;
			});

		}
		html += "</tr></thead><tbody>";
		rows.forEach((r) => {
			html += `<tr><td class="sc-name" title="${esc(r.employee || "")}">${esc(r.label)}${
				r.group ? `<div class="sc-grp">${esc(r.group)}</div>` : ""
			}</td>`;
			d.days.forEach((day) => {
				html += this.cell_html(r, day, r.cells[day]);
			});
			html += "</tr>";
		});
		html += "</tbody>";
		this.$table.html(html);

		this.$empty.toggle(!rows.length).text(__("No sales person matches."));
		this.bind(d);
	}

	cell_html(r, day, c) {
		const esc = frappe.utils.escape_html;
		const sel = day === this.selected ? "sc-selected" : "";
		const base = `data-p="${esc(r.name)}" data-day="${day}"`;
		const wide = this.view === "Week" || this.view === "Day";
		if (wide && !c.slots && this.view === "Day") {
			const cls = ["sc-cell", "sc-" + c.s, c.half ? "sc-half" : "", c.pending ? "sc-pending" : "", sel].join(" ");
			const extra = c.lines && c.lines[0] && !["idle", "future"].includes(c.s) ? " · " + c.lines[0] : "";
			return `<td class="${cls}" ${base}>${esc((c.title || "") + extra)}</td>`;
		}
		if (c.slots) {
			const inner = wide
				? this.timeline_html(c)
				: this.grid_html(c) + `<span class="sc-util" style="width:${c.logged_pct || 0}%"></span>`;
			return `<td class="sc-cell sc-hrs ${sel}" ${base}>${inner}</td>`;
		}
		const cls = ["sc-cell", "sc-" + c.s, c.half ? "sc-half" : "", c.pending ? "sc-pending" : "", sel].join(" ");
		return `<td class="${cls}" ${base}>${SC_STATES[c.s].code}</td>`;
	}

	// Month: one small box per hour, filled left to right by minutes (10:45-11:00 = last quarter)
	grid_html(c) {
		return (
			`<div class="sc-grid" style="grid-template-rows:repeat(${Math.ceil(c.slots.length / 3)},1fr)">` +
			c.slots
				.map((s, i) => {
					const stops = s.segs
						.map((g) => `${SC_COLORS[g.k]} ${(g.s / 60) * 100}% ${(g.e / 60) * 100}%`)
						.join(",");
					return `<i class="sc-slot" style="background:linear-gradient(to right,${stops})" data-i="${i}"></i>`;
				})
				.join("") +
			`</div>`
		);
	}

	// Week / Day: bars placed by real minutes, so 10:45 to 11:50 starts and ends where it should
	timeline_html(c) {
		const esc = frappe.utils.escape_html;
		const day_view = this.view === "Day";
		const bars = c.timeline
			.map((g, i) => {
				const len = g.e - g.s;
				let label = "";
				const has_block = g.b !== null && g.b !== undefined;
				if (len >= (day_view ? 20 : 45)) {
					if (has_block) label = c.blocks[g.b].title + (day_view ? ` · ${c.blocks[g.b].dur}` : "");
					else if (day_view && g.k === "idle") label = __("Idle");
					else if (day_view && g.k === "off") label = __("Not in office");
				}
				return `<i class="sc-bar sc-k-${g.k}" data-seg="${i}"
					style="left:${(g.s / c.total) * 100}%;width:${(len / c.total) * 100}%">${esc(label)}</i>`;
			})
			.join("");
		return `<div class="sc-tl">${bars}</div>`;
	}

	hm(min) {
		const pad = (n) => String(n).padStart(2, "0");
		return `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
	}

	dur(min) {
		const h = Math.floor(min / 60);
		const m = min % 60;
		if (h && m) return __("{0} hr {1} min", [h, m]);
		return h ? __("{0} hr", [h]) : __("{0} min", [m]);
	}

	// tooltip of one stretch of time (a = start minute of the day, z = end minute)
	seg_tip(c, k, b, a, z) {
		const when = `${this.hm(a)} - ${this.hm(z)}`;
		let tip;
		if (b !== null && b !== undefined) {
			const bl = c.blocks[b];
			tip = {
				cls: "sc-k-" + bl.k,
				title: bl.title,
				lines: [`${bl.from} - ${bl.to}  (${bl.dur})`].concat(bl.lines || []),
				docname: bl.docname,
			};
		} else if (k === "off") {
			tip = { cls: "sc-k-off", title: __("Not in office"), lines: [when, __("Outside check-in / check-out time")] };
		} else if (k === "future") {
			tip = { cls: "sc-k-future", title: __("Not yet"), lines: [when] };
		} else if (k === "gap") {
			tip = { cls: "sc-k-gap", title: __("Short gap"), lines: [`${when}  (${this.dur(z - a)})`] };
		} else {
			tip = {
				cls: "sc-k-idle",
				title: __("Idle"),
				lines: [`${when}  (${this.dur(z - a)})`, __("Nothing logged. Click to log what was done.")],
			};
		}
		if (c.day_total) tip.lines = tip.lines.concat([c.day_total]);
		return tip;
	}

	segment_tip(c, d, g) {
		return this.seg_tip(c, g.k, g.b, d.work_start * 60 + g.s, d.work_start * 60 + g.e);
	}

	slot_tip(c, d, i) {
		const slot = c.slots[i];
		const base = slot.h * 60;
		if (slot.segs.length === 1) {
			const g = slot.segs[0];
			return this.seg_tip(c, g.k, g.b, base + g.s, base + g.e);
		}
		const kind = (g) =>
			g.b !== null && g.b !== undefined
				? c.blocks[g.b].title
				: { idle: __("Idle"), off: __("Not in office"), future: __("Not yet"), gap: __("Short gap") }[g.k] || g.k;
		const lines = slot.segs.map((g) => `${this.hm(base + g.s)} - ${this.hm(base + g.e)}  ${kind(g)}`);
		if (c.day_total) lines.push(c.day_total);
		const real = slot.segs.find((g) => g.b !== null && g.b !== undefined && c.blocks[g.b].k !== "auto");
		return {
			cls: "sc-k-" + slot.k,
			title: `${this.hm(base)} - ${this.hm(base + 60)}`,
			lines,
			docname: real ? c.blocks[real.b].docname : null,
		};
	}

	// what a click on a slot / segment should do: open its document, or log time for a free stretch
	click_target(c, d, i, g) {
		const FREE = ["idle", "off", "auto", "gap"];
		if (g !== undefined) {
			const sg = c.timeline[g];
			const a = d.work_start * 60 + sg.s;
			const z = d.work_start * 60 + sg.e;
			if (sg.b !== null && sg.b !== undefined && c.blocks[sg.b].docname)
				return { open: c.blocks[sg.b] };
			return FREE.includes(sg.k) ? { log: [a, z] } : {};
		}
		const slot = c.slots[i];
		const base = slot.h * 60;
		const real = slot.segs.find((x) => x.b !== null && x.b !== undefined && c.blocks[x.b].docname && c.blocks[x.b].k !== "auto");
		if (real) return { open: c.blocks[real.b] };
		const free = slot.segs.find((x) => FREE.includes(x.k));
		return free ? { log: [base + free.s, base + free.e] } : {};
	}

	bind(d) {
		const me = this;
		this.$table.find("th.sc-day").on("click", function () {
			me.selected = $(this).data("day");
			me.render();
		});
		const find = (el) => {
			const $td = $(el).closest("td.sc-cell");
			const row = d.rows.find((r) => r.name === $td.data("p"));
			const day = $td.data("day");
			const $s = $(el).closest("[data-i],[data-seg]");
			return { row, day, c: row.cells[day], i: $s.data("i"), g: $s.data("seg") };
		};
		this.$table
			.find("td.sc-cell")
			.on("mouseenter mousemove", function (e) {
				const { row, day, c, i, g } = find(e.target);
				let tip;
				if (c.slots && g !== undefined) tip = me.segment_tip(c, d, c.timeline[g]);
				else if (c.slots && i !== undefined) tip = me.slot_tip(c, d, i);
				else tip = { cls: "sc-" + c.s, title: c.title, lines: c.lines, docname: c.docname };
				me.show_tip(e, row, day, tip);
			})
			.on("mouseleave", () => this.$tip.hide())
			.on("click", function (e) {
				const { row, day, c, i, g } = find(e.target);
				if (c.slots && (i !== undefined || g !== undefined)) {
					const t = me.click_target(c, d, i, g);
					if (t.open) return frappe.set_route("Form", t.open.doctype, t.open.docname);
					if (t.log && day <= d.today) me.log_dialog(row, day, t.log[0], t.log[1]);
					return;
				}
				if (c.doctype && c.docname) return frappe.set_route("Form", c.doctype, c.docname);
				if (["idle", "work"].includes(c.s) && day <= d.today)
					me.log_dialog(row, day, d.work_start * 60, d.work_start * 60 + 60);
			});
	}

	show_tip(e, row, day, t) {
		const esc = frappe.utils.escape_html;
		this.$tip
			.html(
				`<div class="sc-tip-h ${t.cls}">${esc(row.label)}</div>
				<div class="sc-tip-d">${esc(moment(day).format("ddd, DD MMM YYYY"))}</div>
				<div class="sc-tip-t">${esc(t.title || "")}</div>
				${(t.lines || []).map((l) => `<div class="sc-tip-l">${esc(l)}</div>`).join("")}
				${t.docname ? `<div class="sc-tip-l text-muted">${__("Click to open")} ${esc(t.docname)}</div>` : ""}`
			)
			.show();
		const w = this.$tip.outerWidth();
		const h = this.$tip.outerHeight();
		let x = e.clientX + 14;
		let y = e.clientY + 14;
		if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
		if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
		this.$tip.css({ left: Math.max(8, x), top: Math.max(8, y) });
	}

	log_dialog(row, day, from_min, to_min) {
		const pad = (n) => String(n).padStart(2, "0");
		const dlg = new frappe.ui.Dialog({
			title: __("Log activity: {0}", [row.label]),
			fields: [
				{ fieldname: "day", fieldtype: "Date", label: __("Date"), default: day, read_only: 1 },
				{
					fieldname: "activity_type",
					fieldtype: "Select",
					label: __("What were you doing?"),
					options: SC_ACTIVITY_TYPES.join("\n"),
					default: SC_ACTIVITY_TYPES[0],
					reqd: 1,
				},
				{ fieldtype: "Column Break" },
				{ fieldname: "from_time", fieldtype: "Time", label: __("From"), default: `${this.hm(from_min)}:00`, reqd: 1 },
				{ fieldname: "to_time", fieldtype: "Time", label: __("To"), default: `${to_min >= 1440 ? "23:59" : this.hm(to_min)}:00`, reqd: 1 },
				{ fieldtype: "Section Break" },
				{ fieldname: "description", fieldtype: "Small Text", label: __("Details (customer, lead, task)") },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				frappe.call({
					method: SC_LOG_API,
					args: {
						sales_person: row.name,
						activity_type: v.activity_type,
						from_time: `${day} ${v.from_time}`,
						to_time: `${day} ${v.to_time}`,
						description: v.description || "",
					},
					callback: () => {
						dlg.hide();
						frappe.show_alert({ message: __("Saved in Timesheet"), indicator: "green" });
						this.refresh();
					},
				});
			},
		});
		dlg.show();
	}

	static css() {
		return `
		.sc-wrap,.sc-tip{--sc-call:#f08a24;--sc-vis:#30a46c;--sc-doc:#8e5bd6;--sc-rep:#13a8a8;--sc-wk:#3e63dd;--sc-leave:#e5484d;--sc-tour:#30a46c;--sc-work:#3e63dd;--sc-idle:#f5d90a;--sc-holiday:#b9bbc6;
			--sc-line:var(--border-color,#e3e3e3);padding:4px 0 24px}
		.sc-title{font-size:20px;font-weight:600}
		.sc-head{margin-bottom:10px}
		.sc-cards{display:flex;flex-wrap:wrap;gap:10px;align-items:stretch;margin-bottom:10px}
		.sc-sel{flex:1 1 100%;font-weight:600;color:var(--text-muted)}
		.sc-card{cursor:pointer;min-width:120px;padding:8px 14px;border-radius:8px;border:1px solid var(--sc-line);
			border-left-width:6px;background:var(--card-bg,#fff)}
		.sc-card.sc-leave{border-left-color:var(--sc-leave)}.sc-card.sc-tour{border-left-color:var(--sc-tour)}
		.sc-card.sc-work{border-left-color:var(--sc-work)}.sc-card.sc-idle{border-left-color:var(--sc-idle)}
		.sc-card.sc-holiday{border-left-color:var(--sc-holiday)}
		.sc-num{font-size:22px;font-weight:700;line-height:1.1}.sc-lbl{font-size:12px;color:var(--text-muted)}
		.sc-legend{display:flex;flex-wrap:wrap;gap:14px;margin:6px 0 10px;font-size:12px}
		.sc-key{display:inline-flex;align-items:center;gap:6px}
		.sc-dot{width:14px;height:14px;border-radius:3px;display:inline-block}
		.sc-dot.sc-leave{background:var(--sc-leave)}.sc-dot.sc-tour{background:var(--sc-tour)}
		.sc-dot.sc-work{background:var(--sc-work)}.sc-dot.sc-idle{background:var(--sc-idle)}
		.sc-dot.sc-holiday{background:var(--sc-holiday)}
		.sc-dot.sc-pending{background:repeating-linear-gradient(45deg,#fff 0 3px,#999 3px 5px)}
		.sc-scroll{overflow:auto;max-height:calc(100vh - 330px);border:1px solid var(--sc-line);border-radius:8px}
		.sc-table{border-collapse:separate;border-spacing:0;width:100%}
		.sc-table th,.sc-table td{border-right:1px solid var(--sc-line);border-bottom:1px solid var(--sc-line);text-align:center}
		.sc-table thead th{position:sticky;top:0;z-index:2;background:var(--subtle-fg,#f7f7f7);font-weight:500;
			min-width:46px;padding:4px 0;cursor:pointer;font-size:12px}
		.sc-dow{font-size:10px;color:var(--text-muted)}
		.sc-table .sc-name{position:sticky;left:0;z-index:1;background:var(--card-bg,#fff);text-align:left;
			min-width:170px;max-width:220px;padding:6px 10px;white-space:nowrap;font-weight:500}
		.sc-table thead .sc-name{z-index:3;background:var(--subtle-fg,#f7f7f7)}
		.sc-grp{font-size:10px;color:var(--text-muted);font-weight:400}
		.sc-day.sc-today{box-shadow:inset 0 -3px 0 var(--primary,#2490ef)}
		.sc-day.sc-selected{background:var(--control-bg,#eee)}
		.sc-day.sc-sun{color:var(--sc-leave)}
		.sc-cell{height:46px;min-width:46px;font-size:12px;font-weight:700;color:#fff;cursor:default}
		.sc-cell.sc-leave{background:var(--sc-leave);cursor:pointer}
		.sc-cell.sc-tour{background:var(--sc-tour);cursor:pointer}
		.sc-cell.sc-work{background:var(--sc-work);cursor:pointer}
		.sc-cell.sc-idle{background:var(--sc-idle);color:#7a6a00}
		.sc-cell.sc-holiday{background:var(--sc-holiday);color:#fff}
		.sc-wide .sc-cell,.sc-wide thead th.sc-day{min-width:170px}
		.sc-day-view .sc-cell,.sc-day-view thead th.sc-dayhead{min-width:820px}
		.sc-day-view .sc-cell{height:56px}
		.sc-day-view .sc-grid,.sc-day-view .sc-bars{height:56px}
		.sc-day-view .sc-bar{font-size:12px;padding:8px 8px}
		.sc-day-view td.sc-cell:not(.sc-hrs){text-align:left;padding-left:14px;font-size:13px}
		.sc-ruler{display:grid;grid-template-columns:repeat(9,1fr);font-size:11px;color:var(--text-muted);text-align:left}
		.sc-ruler span{border-left:1px solid var(--sc-line);padding:4px 0 4px 4px}
		.sc-open-day{margin-left:12px;font-weight:400;cursor:pointer}
		.sc-cell.sc-future{background:#f6f6f6}
		.sc-cell.sc-hrs{padding:0;background:#fff;line-height:0;vertical-align:top}
		.sc-grid{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(3,1fr);gap:1px;height:46px;background:#fff}
		.sc-tl{position:relative;height:46px;background:#fff}
		.sc-day-view .sc-tl{height:56px}
		.sc-tl .sc-bar{position:absolute;top:0;bottom:0;box-sizing:border-box;border-right:1px solid rgba(255,255,255,.75)}
		.sc-cell.sc-hrs{position:relative}
		.sc-util{position:absolute;left:0;bottom:0;height:3px;background:rgba(0,0,0,.55);pointer-events:none}
		.sc-k-gap{background:#f4f4f4}
		.sc-bars{display:grid;grid-template-columns:repeat(9,1fr);gap:1px;height:46px;background:#fff}
		.sc-slot{display:block;cursor:pointer}
		.sc-bar{display:block;font-style:normal;font-size:10px;font-weight:600;color:#fff;text-align:left;padding:3px 4px;
			overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:pointer;line-height:1.2}
		.sc-k-call{background:var(--sc-call)}.sc-k-visit{background:var(--sc-vis)}.sc-k-doc{background:var(--sc-doc)}
		.sc-k-report{background:var(--sc-rep)}.sc-k-work{background:var(--sc-wk)}
		.sc-k-auto{background:repeating-linear-gradient(45deg,#fbd9b0 0 3px,#fff 3px 6px)}
		.sc-k-idle{background:var(--sc-idle);color:#7a6a00}.sc-k-off{background:#dcdde3}.sc-k-future{background:#f1f1f1}
		.sc-bar.sc-k-idle,.sc-bar.sc-k-auto{color:#7a6a00}
		.sc-sep{font-weight:600;color:var(--text-muted);margin-left:8px}
		.sc-tip-h.sc-k-idle{color:#7a6a00}.sc-tip-h.sc-k-future,.sc-tip-h.sc-k-auto{color:#555}
		.sc-cell.sc-half{background-image:linear-gradient(135deg,var(--sc-leave) 50%,var(--sc-idle) 50%)}
		.sc-cell.sc-pending{background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.55) 0 3px,transparent 3px 6px)}
		.sc-cell.sc-selected{box-shadow:inset 0 0 0 2px rgba(0,0,0,.35)}
		.sc-cell:hover{filter:brightness(.92)}
		.sc-tip{position:fixed;z-index:2000;max-width:320px;padding:10px 12px;border-radius:8px;pointer-events:none;
			background:var(--fg-color,#fff);color:var(--text-color,#333);border:1px solid var(--sc-line);
			box-shadow:0 6px 20px rgba(0,0,0,.18);font-size:12px}
		.sc-tip-h{display:inline-block;color:#fff;border-radius:4px;padding:1px 8px;font-weight:600;margin-bottom:4px}
		.sc-tip-h.sc-leave{background:var(--sc-leave)}.sc-tip-h.sc-tour{background:var(--sc-tour)}
		.sc-tip-h.sc-work{background:var(--sc-work)}.sc-tip-h.sc-idle{background:var(--sc-idle);color:#7a6a00}
		.sc-tip-h.sc-holiday{background:var(--sc-holiday)}.sc-tip-h.sc-future{background:#999}
		.sc-tip-d{color:var(--text-muted);margin-bottom:2px}.sc-tip-t{font-weight:600;margin-bottom:4px}
		.sc-tip-l{margin-top:2px;word-break:break-word}
		`;
	}
}
})();
