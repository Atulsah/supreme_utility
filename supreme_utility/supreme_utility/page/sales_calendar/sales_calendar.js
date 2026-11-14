// Sales Calendar: every Sales Person x every day.
// Red = Leave, Green = Tour, Blue = Event / working on something, Yellow = Idle, Grey = Holiday.
(function () {

frappe.pages["sales-calendar"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sales Calendar"),
		single_column: true,
	});
	wrapper.sales_calendar = new SalesCalendar(page, wrapper);
};

frappe.pages["sales-calendar"].on_page_show = function (wrapper) {
	if (wrapper.sales_calendar) wrapper.sales_calendar.refresh();
};

const SC_API = "supreme_utility.supreme_utility.page.sales_calendar.sales_calendar.get_schedule";
const SC_STATES = {
	leave: { label: __("On Leave"), code: "L" },
	tour: { label: __("On Tour"), code: "T" },
	work: { label: __("Working (Event)"), code: "W" },
	idle: { label: __("Idle"), code: "" },
	holiday: { label: __("Holiday / Off"), code: "H" },
};

class SalesCalendar {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.view = "Month";
		this.anchor = frappe.datetime.str_to_obj(frappe.datetime.get_today());
		this.selected = frappe.datetime.get_today();
		this.data = null;
		this.make_controls();
		this.make_body();
		this.refresh();
	}

	make_controls() {
		const p = this.page;
		this.group_field = p.add_field({
			fieldname: "sales_person_group",
			label: __("Sales Person / Group"),
			fieldtype: "Link",
			options: "Sales Person",
			change: () => this.refresh(),
		});
		this.view_field = p.add_field({
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Month", "Week", "Fortnight"],
			default: "Month",
			change: () => {
				this.view = this.view_field.get_value() || "Month";
				this.refresh();
			},
		});
		this.show_field = p.add_field({
			fieldname: "show",
			label: __("Show on selected day"),
			fieldtype: "Select",
			options: ["", "On Leave", "On Tour", "Working (Event)", "Idle", "Holiday / Off"],
			change: () => this.render(),
		});
		this.search_field = p.add_field({
			fieldname: "search",
			label: __("Search"),
			fieldtype: "Data",
			change: () => this.render(),
		});
		p.set_primary_action(__("Today"), () => {
			this.anchor = frappe.datetime.str_to_obj(frappe.datetime.get_today());
			this.selected = frappe.datetime.get_today();
			this.refresh();
		});
		p.add_inner_button(__("Previous"), () => this.shift(-1));
		p.add_inner_button(__("Next"), () => this.shift(1));
		p.add_menu_item(__("Refresh"), () => this.refresh());
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
		if (this.view === "Week") {
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
		return [frappe.datetime.obj_to_str(start), frappe.datetime.obj_to_str(end)];
	}

	shift(dir) {
		const a = this.anchor;
		if (this.view === "Week") this.anchor = new Date(a.getFullYear(), a.getMonth(), a.getDate() + 7 * dir);
		else if (this.view === "Fortnight")
			this.anchor = new Date(a.getFullYear(), a.getMonth(), a.getDate() + 14 * dir);
		else this.anchor = new Date(a.getFullYear(), a.getMonth() + dir, 1);
		this.refresh();
	}

	refresh() {
		const [from_date, to_date] = this.range();
		frappe.call({
			method: SC_API,
			args: {
				from_date,
				to_date,
				sales_person_group: this.group_field.get_value() || null,
			},
			freeze: true,
			freeze_message: __("Loading..."),
			callback: (r) => {
				this.data = r.message;
				if (!this.data.days.includes(this.selected)) {
					this.selected = this.data.days.includes(this.data.today)
						? this.data.today
						: this.data.days[0];
				}
				this.render();
			},
		});
	}

	render() {
		const d = this.data;
		if (!d) return;
		const esc = frappe.utils.escape_html;
		const [from_date, to_date] = [d.from_date, d.to_date];
		const title =
			this.view === "Month"
				? moment(from_date).format("MMMM YYYY")
				: `${frappe.datetime.str_to_user(from_date)} - ${frappe.datetime.str_to_user(to_date)}`;
		this.$head.html(`<div class="sc-title">${esc(title)}</div>
			<div class="text-muted small">${__("{0} sales persons", [d.rows.length])}. ${__(
			"Click a date to see who is where on that day. Click a coloured cell to open it."
		)}</div>`);

		// summary of the selected day
		const sum = d.summary[this.selected] || {};
		this.$cards.html(
			`<div class="sc-sel">${esc(moment(this.selected).format("dddd, DD MMM YYYY"))}</div>` +
				["leave", "tour", "work", "idle", "holiday"]
					.map(
						(k) => `<div class="sc-card sc-${k}" data-state="${k}">
					<div class="sc-num">${sum[k] || 0}</div><div class="sc-lbl">${esc(SC_STATES[k].label)}</div></div>`
					)
					.join("")
		);
		this.$cards.find(".sc-card").on("click", (e) => {
			const label = SC_STATES[$(e.currentTarget).data("state")].label;
			this.show_field.set_value(this.show_field.get_value() === label ? "" : label);
		});

		this.$legend.html(
			["leave", "tour", "work", "idle", "holiday"]
				.map((k) => `<span class="sc-key"><i class="sc-dot sc-${k}"></i>${esc(SC_STATES[k].label)}</span>`)
				.join("") + `<span class="sc-key"><i class="sc-dot sc-pending"></i>${__("Not approved yet")}</span>`
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

		let html = `<thead><tr><th class="sc-name">${__("Sales Person")}</th>`;
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
		html += "</tr></thead><tbody>";
		rows.forEach((r) => {
			html += `<tr><td class="sc-name" title="${esc(r.employee || "")}">${esc(r.label)}${
				r.group ? `<div class="sc-grp">${esc(r.group)}</div>` : ""
			}</td>`;
			d.days.forEach((day) => {
				const c = r.cells[day];
				const cls = ["sc-cell", "sc-" + c.s, c.half ? "sc-half" : "", c.pending ? "sc-pending" : "",
					day === this.selected ? "sc-selected" : ""].join(" ");
				html += `<td class="${cls}" data-p="${esc(r.name)}" data-day="${day}">${SC_STATES[c.s].code}</td>`;
			});
			html += "</tr>";
		});
		html += "</tbody>";
		this.$table.html(html);

		this.$empty.toggle(!rows.length).text(__("No sales person matches."));
		this.bind(d);
	}

	bind(d) {
		const me = this;
		this.$table.find("th.sc-day").on("click", function () {
			me.selected = $(this).data("day");
			me.render();
		});
		this.$table
			.find("td.sc-cell")
			.on("mouseenter mousemove", function (e) {
				const row = d.rows.find((r) => r.name === $(this).data("p"));
				const c = row.cells[$(this).data("day")];
				me.show_tip(e, row, $(this).data("day"), c);
			})
			.on("mouseleave", () => this.$tip.hide())
			.on("click", function () {
				const row = d.rows.find((r) => r.name === $(this).data("p"));
				const c = row.cells[$(this).data("day")];
				if (c.doctype && c.docname) frappe.set_route("Form", c.doctype, c.docname);
			});
	}

	show_tip(e, row, day, c) {
		const esc = frappe.utils.escape_html;
		this.$tip
			.html(
				`<div class="sc-tip-h sc-${c.s}">${esc(row.label)}</div>
				<div class="sc-tip-d">${esc(moment(day).format("ddd, DD MMM YYYY"))}</div>
				<div class="sc-tip-t">${esc(c.title)}</div>
				${(c.lines || []).map((l) => `<div class="sc-tip-l">${esc(l)}</div>`).join("")}
				${c.docname ? `<div class="sc-tip-l text-muted">${__("Click to open")} ${esc(c.docname)}</div>` : ""}`
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

	static css() {
		return `
		.sc-wrap,.sc-tip{--sc-leave:#e5484d;--sc-tour:#30a46c;--sc-work:#3e63dd;--sc-idle:#f5d90a;--sc-holiday:#b9bbc6;
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
			min-width:30px;padding:4px 0;cursor:pointer;font-size:12px}
		.sc-dow{font-size:10px;color:var(--text-muted)}
		.sc-table .sc-name{position:sticky;left:0;z-index:1;background:var(--card-bg,#fff);text-align:left;
			min-width:170px;max-width:220px;padding:6px 10px;white-space:nowrap;font-weight:500}
		.sc-table thead .sc-name{z-index:3;background:var(--subtle-fg,#f7f7f7)}
		.sc-grp{font-size:10px;color:var(--text-muted);font-weight:400}
		.sc-day.sc-today{box-shadow:inset 0 -3px 0 var(--primary,#2490ef)}
		.sc-day.sc-selected{background:var(--control-bg,#eee)}
		.sc-day.sc-sun{color:var(--sc-leave)}
		.sc-cell{height:34px;min-width:30px;font-size:12px;font-weight:700;color:#fff;cursor:default}
		.sc-cell.sc-leave{background:var(--sc-leave);cursor:pointer}
		.sc-cell.sc-tour{background:var(--sc-tour);cursor:pointer}
		.sc-cell.sc-work{background:var(--sc-work);cursor:pointer}
		.sc-cell.sc-idle{background:var(--sc-idle);color:#7a6a00}
		.sc-cell.sc-holiday{background:var(--sc-holiday);color:#fff}
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
		.sc-tip-h.sc-holiday{background:var(--sc-holiday)}
		.sc-tip-d{color:var(--text-muted);margin-bottom:2px}.sc-tip-t{font-weight:600;margin-bottom:4px}
		.sc-tip-l{margin-top:2px;word-break:break-word}
		`;
	}
}
})();
