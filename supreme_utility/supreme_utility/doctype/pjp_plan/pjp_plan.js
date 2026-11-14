// Copyright (c) 2026, SGPL and contributors
// For license information, please see license.txt

const PJP_METHODS = "supreme_utility.supreme_utility.doctype.pjp_plan.pjp_plan";
const LEAD_SYNC_METHOD = "supreme_utility.lead_sync.sync_leads";

// ---------------------------------------------------------------------------
// Date limits for the date pickers inside the child tables.
// Applied through the picker's own onRenderCell option, so nothing touches the
// picker while a date is being selected. Days outside the range are disabled.
// ---------------------------------------------------------------------------
(function () {
	const day = (v) => (v ? moment(String(v).substring(0, 10), "YYYY-MM-DD").toDate() : null);
	const today = () => moment().startOf("day").toDate();
	const earlier = (a, b) => (a && b ? (a < b ? a : b) : a || b);

	// Ad-Hoc Visit: the claim period (From Date .. To Date) is the trip period
	const trip_start = (doc) => (doc.claim_mode === "Ad-Hoc Visit" ? doc.from_date : doc.actual_start);
	const trip_end = (doc) => (doc.claim_mode === "Ad-Hoc Visit" ? doc.to_date : doc.actual_end);

	const LIMITS = {
		// Planned Date: From Date .. To Date
		"PJP Visit.planned_date": (doc) => ({ min: day(doc.from_date), max: day(doc.to_date) }),
		// Actual Date of Visit and Expense Date: trip start .. trip end (never in the future)
		"PJP Visit.actual_visit_date": (doc) => ({
			min: day(trip_start(doc)),
			max: earlier(day(trip_end(doc)), today()),
		}),
		"PJP Actual Cost.expense_date": (doc) => ({
			min: day(trip_start(doc)),
			max: earlier(day(trip_end(doc)), today()),
		}),
	};

	const proto = frappe.ui.form.ControlDate && frappe.ui.form.ControlDate.prototype;
	if (!proto || proto.__pjp_patched || !proto.set_date_options) return;
	proto.__pjp_patched = true;

	const original = proto.set_date_options;
	proto.set_date_options = function () {
		original.apply(this, arguments);
		try {
			const limit = LIMITS[`${this.df.parent}.${this.df.fieldname}`];
			const opts = this.datepicker_options;
			if (!limit || !opts) return;

			const get_range = () =>
				window.cur_frm && cur_frm.doctype === "PJP Plan" ? limit(cur_frm.doc) : {};

			opts.onRenderCell = (date, cell_type) => {
				if (cell_type !== "day") return {};
				const r = get_range();
				return (r.min && date < r.min) || (r.max && date > r.max) ? { disabled: true } : {};
			};

			// Open on the allowed month instead of today's month
			const r0 = get_range();
			if (r0.min && today() < r0.min) opts.startDate = r0.min;
			else if (r0.max && today() > r0.max) opts.startDate = r0.max;

			// Re-draw the days each time the picker opens, so changed limits apply
			const on_show = opts.onShow;
			opts.onShow = (inst, done) => {
				if (on_show) on_show(inst, done);
				if (!done) {
					try {
						inst.views[inst.currentView]._render();
					} catch (e) {
						/* ignore */
					}
				}
			};
		} catch (e) {
			console.warn("PJP date limits not applied", e);
		}
	};
})();

// ---------------------------------------------------------------------------
// Payment / return status of the linked Employee Advance and Expense Claim
// ---------------------------------------------------------------------------
const PJP_STATUS_COLOR = {
	Paid: "green",
	Claimed: "green",
	Returned: "green",
	"Partly Claimed and Returned": "blue",
	Unpaid: "orange",
	Submitted: "orange",
	Draft: "gray",
	Rejected: "red",
	Cancelled: "red",
};

function pjp_settlement_html(s) {
	const money = (v) => format_currency(flt(v), s.currency || frappe.defaults.get_default("currency"));
	const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
	const row = (label, value, cls) =>
		`<tr class="${cls || ""}"><td class="text-muted" style="width: 55%;">${esc(label)}</td>` +
		`<td class="text-right"><b>${value}</b></td></tr>`;
	const block = (title, doctype, d, rows) =>
		`<div class="col-sm-6" style="margin-bottom: 10px;">
			<div style="margin-bottom: 6px;"><b>${esc(title)}</b>
				${frappe.utils.get_form_link(doctype, d.name, true)}
				<span class="indicator-pill ${PJP_STATUS_COLOR[d.status] || "gray"}">${esc(__(d.status || ""))}</span></div>
			<table class="table table-bordered table-sm" style="margin-bottom: 0;">${rows}</table>
		</div>`;

	const adv = s.advance;
	const clm = s.claim;
	let blocks = "";
	let alerts = "";

	if (adv) {
		blocks += block(
			__("Employee Advance"),
			"Employee Advance",
			adv,
			row(__("Advance Amount"), money(adv.advance_amount)) +
				row(__("Paid to Employee"), money(adv.paid_amount)) +
				row(__("Adjusted in Expense Claim"), money(adv.adjusted)) +
				row(__("Returned by Employee"), money(adv.returned), flt(adv.returned) > 0 ? "text-success" : "") +
				(clm ? row(__("Still to be Returned"), money(adv.to_return), flt(adv.to_return) > 0 ? "text-warning" : "") : "")
		);
		if (flt(adv.returned) > 0) {
			alerts += `<div class="alert alert-success" style="margin-bottom: 6px;">${__(
				"Extra advance returned by the employee: {0}",
				[money(adv.returned)]
			)}</div>`;
		}
		if (flt(adv.to_return) > 0) {
			alerts += `<div class="alert alert-warning" style="margin-bottom: 6px;">${__(
				"Advance is more than the expense. Employee still has to return {0}.",
				[money(adv.to_return)]
			)}</div>`;
		}
	}

	if (clm) {
		blocks += block(
			__("Expense Claim"),
			"Expense Claim",
			clm,
			row(__("Total Claimed"), money(clm.total_claimed_amount)) +
				row(__("Total Sanctioned"), money(clm.total_sanctioned_amount)) +
				row(__("Advance Adjusted"), money(clm.total_advance_amount)) +
				row(__("Net Payable to Employee"), money(clm.net_payable)) +
				row(__("Paid to Employee"), money(clm.paid), flt(clm.paid) > 0 ? "text-success" : "") +
				(clm.docstatus === 1 ? row(__("Balance Due"), money(clm.balance_due), flt(clm.balance_due) > 0 ? "text-warning" : "") : "")
		);
	}

	return `<div class="row">${blocks}</div>${alerts}`;
}

// Trip period used to validate dates. Ad-Hoc Visit: From Date .. To Date.
const pjp_trip_range = (frm) =>
	frm.doc.claim_mode === "Ad-Hoc Visit"
		? { start: frm.doc.from_date, end: frm.doc.to_date }
		: {
				start: frm.doc.actual_start && String(frm.doc.actual_start).substring(0, 10),
				end: frm.doc.actual_end && String(frm.doc.actual_end).substring(0, 10),
		  };

frappe.ui.form.on("PJP Plan", {
	setup(frm) {
		frm.set_query("salesperson", () => ({ filters: { enabled: 1, is_group: 0 } }));
		frm.set_query("employee", () => ({ filters: { status: "Active" } }));

		// Ad-Hoc Visit: an existing, submitted and still unclaimed advance of this employee
		frm.set_query("employee_advance", () => ({
			filters: {
				employee: frm.doc.employee,
				docstatus: 1,
				paid_amount: [">", 0],
				status: ["not in", ["Claimed", "Returned", "Partly Claimed and Returned"]],
			},
		}));

		// Party filters on the visit rows
		frm.set_query("sales_order", "visits", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			const filters = { docstatus: ["!=", 2] };
			if (row.visit_with === "Customer" && row.party) filters.customer = row.party;
			return { filters };
		});
		frm.set_query("opportunity", "visits", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			const filters = {};
			if (["Customer", "Lead"].includes(row.visit_with) && row.party) {
				filters.opportunity_from = row.visit_with;
				filters.party_name = row.party;
			}
			return { filters };
		});
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			const company = frappe.defaults.get_user_default("Company");
			if (company) frm.set_value("company", company);
		}

		if (frm.is_new() && !frm.doc.salesperson) {
			frappe.call({
				method: `${PJP_METHODS}.get_default_sales_person`,
				callback: (r) => {
					const d = r.message || {};
					const set_employee = () => {
						if (!frm.doc.employee && d.employee) frm.set_value("employee", d.employee);
					};
					if (d.salesperson) {
						frm.set_value("salesperson", d.salesperson).then(() => frappe.after_ajax(set_employee));
					} else {
						set_employee();
					}
				},
			});
		}
	},

	salesperson(frm) {
		if (!frm.doc.salesperson) return;
		frappe.db.get_value("Sales Person", frm.doc.salesperson, "employee").then((r) => {
			if (!(r.message || {}).employee) {
				frappe.show_alert(
					{
						message: __(
							"Sales Person {0} has no Employee linked. Select the Employee manually here, or set it on the Sales Person.",
							[frm.doc.salesperson]
						),
						indicator: "orange",
					},
					10
				);
			}
		});
	},

	refresh(frm) {
		frm.trigger("toggle_actual_columns");
		frm.events.highlight_unplanned(frm);
		frm.events.render_settlement(frm);

		// Closed = read-only. Ad-Hoc Visit is also read-only once the Sales Manager approved it.
		if (
			frm.doc.docstatus === 1 &&
			(frm.doc.expense_claim || frm.doc.claim_mode === "Ad-Hoc Visit")
		) {
			frm.set_read_only();
			frm.disable_save();
		}

		// Sync Leads: link / create Leads, update status and notes, create Opportunity (re-runnable)
		if (frm.doc.docstatus === 1 && (frm.doc.visits || []).some((r) => r.visit_with === "Lead")) {
			frm.add_custom_button(__("Sync Leads"), () => {
				if (frm.is_dirty()) {
					frappe.msgprint(__("Please save (Update) the PJP first."));
					return;
				}
				frappe.call({
					method: LEAD_SYNC_METHOD,
					args: { pjp_name: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}

		// Create Expense Claim: submitted, actual cost rows exist, no claim linked yet
		if (
			frm.doc.docstatus === 1 &&
			(frm.doc.actual_costs || []).length &&
			!frm.doc.expense_claim
		) {
			frm.add_custom_button(__("Create Expense Claim"), () => {
				if (frm.is_dirty()) {
					frappe.msgprint(__("Please save (Update) the PJP first, then create the Expense Claim."));
					return;
				}
				frappe.call({
					method: `${PJP_METHODS}.get_claim_preview`,
					args: { pjp_name: frm.doc.name },
					callback: (res) => {
						const p = res.message || {};
						const fmt = (v) => format_currency(v);
						let msg = __("Create one Expense Claim for all actual costs of this PJP?");
						msg += `<br><br><b>${__("Expense claimed")}:</b> ${fmt(p.claim_total)}`;
						if (p.has_advance && flt(p.advance_adjusted) > 0) {
							msg += `<br><b>${__("Advance adjusted")}:</b> ${fmt(p.advance_adjusted)}`;
							msg += `<br><b>${__("Net payable to employee")}:</b> ${fmt(p.net_payable)}`;
							if (flt(p.excess_to_return) > 0) {
								msg += `<br><span class="text-warning"><b>${__("Employee must return")}:</b> ${fmt(
									p.excess_to_return
								)} (${__("advance more than expense")})</span>`;
							}
						} else if (p.has_advance) {
							msg += `<br><span class="text-warning">${__(
								"Employee Advance {0} will NOT be adjusted: {1}. Submit and pay it first, or add it later in the claim with Get Advances Paid.",
								[p.employee_advance, p.reason]
							)}</span>`;
						}
						frappe.confirm(msg, () => {
							frappe.call({
								method: `${PJP_METHODS}.make_expense_claim`,
								args: { pjp_name: frm.doc.name },
								freeze: true,
								callback: (r) => {
									if (r.message) frappe.set_route("Form", "Expense Claim", r.message);
								},
							});
						});
					},
				});
			}).addClass("btn-primary");
		}

		// Create Employee Advance: approved plan, trip not started (status Planned), none yet
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.status === "Planned" &&
			!frm.doc.employee_advance &&
			!frm.doc.expense_claim &&
			flt(frm.doc.total_estimated_cost) > 0
		) {
			frm.add_custom_button(__("Create Employee Advance"), () => {
				if (frm.is_dirty()) {
					frappe.msgprint(__("Please save the PJP first."));
					return;
				}
				frappe.prompt(
					[
						{
							fieldname: "advance_amount",
							label: __("Advance Amount"),
							fieldtype: "Currency",
							reqd: 1,
							default: frm.doc.total_estimated_cost,
							description: __("Up to the Total Estimated Cost: {0}", [
								format_currency(frm.doc.total_estimated_cost),
							]),
						},
					],
					(values) => {
						frappe.call({
							method: `${PJP_METHODS}.make_employee_advance`,
							args: { pjp_name: frm.doc.name, advance_amount: values.advance_amount },
							freeze: true,
							callback: (r) => {
								if (r.message) frappe.set_route("Form", "Employee Advance", r.message);
							},
						});
					},
					__("Employee Advance"),
					__("Create")
				);
			});
		}
	},

	// Amounts, payment and return status of the linked Employee Advance / Expense Claim (always read live)
	render_settlement(frm) {
		const field = frm.fields_dict.settlement_html;
		if (!field) return;
		if (frm.is_new() || !(frm.doc.employee_advance || frm.doc.expense_claim)) {
			field.$wrapper.empty();
			return;
		}
		frappe.call({
			method: `${PJP_METHODS}.get_settlement`,
			args: { pjp_name: frm.doc.name },
			callback: (r) => field.$wrapper.html(pjp_settlement_html(r.message || {})),
		});
	},

	actual_start(frm) {
		frm.events.check_actual_dates(frm);
	},

	actual_end(frm) {
		frm.events.check_actual_dates(frm);
	},

	check_actual_dates(frm) {
		if (frm.doc.actual_start && frm.doc.actual_end && frm.doc.actual_end < frm.doc.actual_start) {
			frappe.msgprint(__("Actual End cannot be before Actual Start."));
			frm.set_value("actual_end", null);
			return;
		}
		// Warn about expense rows that now fall outside the trip
		const start = String(frm.doc.actual_start || "").substring(0, 10);
		const end = String(frm.doc.actual_end || "").substring(0, 10);
		const bad = (frm.doc.actual_costs || [])
			.filter(
				(r) =>
					r.expense_date &&
					((start && frappe.datetime.get_diff(r.expense_date, start) < 0) ||
						(end && frappe.datetime.get_diff(end, r.expense_date) < 0))
			)
			.map((r) => r.idx);
		if (bad.length) {
			frappe.msgprint(
				__("Actual Cost row(s) {0} have an Expense Date outside the trip dates. Please correct them.", [
					bad.join(", "),
				])
			);
		}
		const bad_visits = (frm.doc.visits || [])
			.filter(
				(r) =>
					r.actual_visit_date &&
					((start && frappe.datetime.get_diff(r.actual_visit_date, start) < 0) ||
						(end && frappe.datetime.get_diff(end, r.actual_visit_date) < 0))
			)
			.map((r) => r.idx);
		if (bad_visits.length) {
			frappe.msgprint(
				__("Visit row(s) {0} have an Actual Date of Visit outside the trip dates. Please correct them.", [
					bad_visits.join(", "),
				])
			);
		}
	},

	// Unplanned visits (added during/after the trip) are shown with an orange marker
	highlight_unplanned(frm) {
		setTimeout(() => {
			const grid = frm.fields_dict.visits && frm.fields_dict.visits.grid;
			if (!grid) return;
			(grid.grid_rows || []).forEach((gr) =>
				$(gr.row).toggleClass("pjp-unplanned-row", !!(gr.doc && cint(gr.doc.is_unplanned)))
			);
		}, 250);
	},

	toggle_actual_columns(frm) {
		// Each state gets its own column layout that adds up to exactly 10 units,
		// otherwise the grid overflows and the rows misalign.
		const grid = frm.fields_dict.visits && frm.fields_dict.visits.grid;
		if (!grid) return;

		const layout =
			frm.doc.claim_mode === "Ad-Hoc Visit"
				? // Ad-Hoc Visit: finished visits only (no plan columns)
				  { visit_with: 1, party_name: 2, organization_name: 1, purpose: 2, actual_visit_date: 2, met_with: 1, order_value: 1 }
				: frm.doc.docstatus === 1
				? // After approval: identity + actual result columns
				  { party_name: 2, organization_name: 1, purpose: 2, planned_date: 1, actual_visit_date: 2, visit_result: 1, order_value: 1 }
				: // Draft: planning columns
				  { visit_with: 1, party: 2, party_name: 2, purpose: 2, planned_date: 2, expected_business: 1 };

		[
			"visit_with", "party", "party_name", "organization_name", "purpose", "planned_date", "expected_business",
			"actual_visit_date", "met_with", "visit_result", "order_value",
		].forEach((fn) => {
			const cols = layout[fn] || 0;
			grid.update_docfield_property(fn, "in_list_view", cols ? 1 : 0);
			if (cols) grid.update_docfield_property(fn, "columns", cols);
		});
		grid.reset_grid();
	},

	claim_mode(frm) {
		frm.trigger("toggle_actual_columns");
	},

	from_date(frm) {
		frm.events.check_dates(frm);
	},

	to_date(frm) {
		frm.events.check_dates(frm);
	},

	check_dates(frm) {
		if (
			frm.doc.from_date &&
			frm.doc.to_date &&
			frappe.datetime.get_diff(frm.doc.to_date, frm.doc.from_date) < 0
		) {
			frappe.msgprint(__("To Date cannot be before From Date."));
			frm.set_value("to_date", null);
		}
	},

	// Live totals (the server recalculates on save, this is only for convenience)
	calculate_totals(frm) {
		const sum = (rows, fn) => (rows || []).reduce((t, r) => t + flt(r[fn]), 0);
		const estimated = sum(frm.doc.estimate_costs, "estimated_amount");
		const expected = sum(frm.doc.visits, "expected_business");
		const actual = sum(frm.doc.actual_costs, "actual_amount");
		const order = sum(frm.doc.visits, "order_value");

		const values = {
			total_estimated_cost: estimated,
			total_expected_business: expected,
			total_actual_cost: actual,
			total_order_value: order,
			total_unplanned_business: sum(
				(frm.doc.visits || []).filter((r) => cint(r.is_unplanned)),
				"order_value"
			),
			cost_variance: actual && frm.doc.claim_mode !== "Ad-Hoc Visit" ? estimated - actual : 0,
			roi_percent: actual ? flt(((order - actual) / actual) * 100, 2) : null,
		};
		Object.keys(values).forEach((k) => {
			if (flt(frm.doc[k]) !== flt(values[k]) || (values[k] === null) !== (frm.doc[k] == null)) {
				frm.set_value(k, values[k]);
			}
		});
	},
});

frappe.dom.set_style(
	".pjp-unplanned-row .data-row { background-color: var(--yellow-50, #fffbeb); box-shadow: inset 3px 0 0 var(--orange-400, #fb923c); }",
	"pjp-unplanned-style"
);

frappe.ui.form.on("PJP Visit", {
	visits_add(frm, cdt, cdn) {
		// Ad-Hoc Visit: every row is a finished visit; "Other" lets the user type a company name
		if (frm.doc.claim_mode === "Ad-Hoc Visit") {
			frappe.model.set_value(cdt, cdn, "visit_with", "Other");
			return;
		}
		// A row added after approval is an unplanned visit
		if (frm.doc.docstatus !== 1) return;
		frappe.model.set_value(cdt, cdn, "is_unplanned", 1);
		frappe.model.set_value(cdt, cdn, "visit_with", "Customer");
		frappe.show_alert(
			{
				message: __("Unplanned visit: Planned Date and Expected Business are not required. Enter the Actual Date of Visit and result."),
				indicator: "blue",
			},
			8
		);
		setTimeout(() => {
			const grid_row = frm.fields_dict.visits.grid.grid_rows_by_docname[cdn];
			if (grid_row) grid_row.toggle_view(true);
			frm.events.highlight_unplanned(frm);
		}, 150);
	},

	form_render: (frm) => frm.events.highlight_unplanned(frm),

	visits_remove: (frm) => frm.events.calculate_totals(frm),
	expected_business: (frm) => frm.events.calculate_totals(frm),
	order_value: (frm) => frm.events.calculate_totals(frm),

	visit_with(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "party", null);
		frappe.model.set_value(cdt, cdn, "party_name", null);
		frappe.model.set_value(cdt, cdn, "organization_name", null);
	},

	party(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const name_field = { Customer: "customer_name", Lead: "lead_name", Contact: "full_name" }[row.visit_with];
		if (!row.party) {
			// Lead: leave any typed name alone; others: clear
			if (!["Lead", "Other"].includes(row.visit_with)) frappe.model.set_value(cdt, cdn, "party_name", null);
			return;
		}
		if (!name_field) return;
		// A Lead is a person: Customer Name = the person, Organization Name = the person's company
		const fields = row.visit_with === "Lead" ? [name_field, "company_name"] : [name_field];
		frappe.db.get_value(row.visit_with, row.party, fields).then((r) => {
			const m = r.message || {};
			frappe.model.set_value(cdt, cdn, "party_name", m[name_field] || row.party);
			if (row.visit_with === "Lead") frappe.model.set_value(cdt, cdn, "organization_name", m.company_name || null);
		});
	},

	planned_date(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.planned_date || !frm.doc.from_date || !frm.doc.to_date) return;
		if (
			frappe.datetime.get_diff(row.planned_date, frm.doc.from_date) < 0 ||
			frappe.datetime.get_diff(frm.doc.to_date, row.planned_date) < 0
		) {
			frappe.msgprint(__("Planned Date must fall between From Date and To Date."));
			frappe.model.set_value(cdt, cdn, "planned_date", null);
		}
	},

	actual_visit_date(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.actual_visit_date) return;
		const clear = (msg) => {
			frappe.msgprint(msg);
			frappe.model.set_value(cdt, cdn, "actual_visit_date", null);
		};
		const { start, end } = pjp_trip_range(frm);
		if (!start || !end) {
			return clear(__("Enter the trip period (Actual Start / Actual End, or From / To Date) first."));
		}
		if (frappe.datetime.get_diff(row.actual_visit_date, start) < 0 || frappe.datetime.get_diff(end, row.actual_visit_date) < 0) {
			return clear(
				__("Actual Date of Visit must be between {0} and {1} (the actual trip dates).", [
					frappe.datetime.str_to_user(start),
					frappe.datetime.str_to_user(end),
				])
			);
		}
		if (frappe.datetime.get_diff(row.actual_visit_date, frappe.datetime.get_today()) > 0) {
			clear(__("Actual Date of Visit cannot be in the future."));
		}
	},
});

frappe.ui.form.on("PJP Estimate Cost", {
	estimate_costs_remove: (frm) => frm.events.calculate_totals(frm),
	estimated_amount: (frm) => frm.events.calculate_totals(frm),
});

frappe.ui.form.on("PJP Actual Cost", {
	expense_date(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.expense_date) return;
		const clear = (msg) => {
			frappe.msgprint(msg);
			frappe.model.set_value(cdt, cdn, "expense_date", null);
		};
		const { start, end } = pjp_trip_range(frm);
		if (!start || !end) {
			return clear(__("Enter the trip period (Actual Start / Actual End, or From / To Date) first."));
		}
		if (frappe.datetime.get_diff(row.expense_date, start) < 0 || frappe.datetime.get_diff(end, row.expense_date) < 0) {
			return clear(
				__("Expense Date must be between {0} and {1} (the actual trip dates).", [
					frappe.datetime.str_to_user(start),
					frappe.datetime.str_to_user(end),
				])
			);
		}
		if (frappe.datetime.get_diff(row.expense_date, frappe.datetime.get_today()) > 0) {
			clear(__("Expense Date cannot be in the future."));
		}
	},

	actual_costs_remove: (frm) => frm.events.calculate_totals(frm),
	actual_amount: (frm) => frm.events.calculate_totals(frm),
});
