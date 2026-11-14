// Copyright (c) 2026, SGPL and contributors
// For license information, please see license.txt

frappe.query_reports["PJP Cycle"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "salesperson", label: __("Salesperson"), fieldtype: "Link", options: "Sales Person" },
		{ fieldname: "territory", label: __("Territory"), fieldtype: "Link", options: "Territory" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nDraft\nPlanned\nVisited\nClosed\nCancelled",
		},
		{ fieldname: "campaign", label: __("Campaign"), fieldtype: "Link", options: "Campaign" },
		{
			fieldname: "claim_mode",
			label: __("Claim Mode"),
			fieldtype: "Select",
			options: "\nPJP Plan\nAd-Hoc Visit",
		},
	],

	// Overspend (negative variance) shown in red
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && ["cost_variance", "variance_percent"].includes(column.fieldname) && flt(data[column.fieldname]) < 0) {
			value = `<span style="color: var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
