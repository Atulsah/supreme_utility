frappe.listview_settings["Catalogue Design"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Make Catalogue"), () => frappe.set_route("design-catalogue"));
		if (frappe.model.can_create("Catalogue Design")) {
			listview.page.add_inner_button(__("Import Images from ZIP"), () => import_zip(listview));
		}
	},
};

function import_zip(listview) {
	const d = new frappe.ui.Dialog({
		title: __("Import Images from ZIP"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Each picture in the ZIP becomes one design. The file name is the Design No, e.g. <b>149 Red.jpg</b> → Design No <b>149 Red</b>."
				)}</p>`,
			},
			{ fieldname: "zip_file", fieldtype: "Attach", label: __("ZIP File"), reqd: 1 },
			{ fieldname: "brand", fieldtype: "Link", options: "Brand", label: __("Brand") },
			{ fieldname: "quality", fieldtype: "Data", label: __("Quality"), description: __("e.g. Tarang, Rangoli") },
			{ fieldname: "update_existing", fieldtype: "Check", label: __("Replace image of designs that already exist"), default: 0 },
		],
		primary_action_label: __("Import"),
		primary_action(v) {
			d.hide();
			frappe
				.call({
					method: "supreme_utility.catalogue.import_designs_from_zip",
					args: v,
					freeze: true,
					freeze_message: __("Importing designs..."),
				})
				.then((r) => {
					const m = r.message || {};
					const lines = [__("Created: {0}", [m.created || 0]), __("Updated: {0}", [m.updated || 0]), __("Skipped (already there): {0}", [m.skipped || 0])];
					if ((m.not_images || []).length) lines.push(__("Not images: {0}", [frappe.utils.escape_html(m.not_images.join(", "))]));
					frappe.msgprint({ title: __("Import done"), message: lines.join("<br>"), indicator: "green" });
					listview.refresh();
				});
		},
	});
	d.show();
}
