// Item: "Design Catalogue" button on template items (Quality) and their variants.
frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		const quality = frm.doc.has_variants ? frm.doc.name : frm.doc.variant_of;
		if (!quality) return;
		frm.add_custom_button(
			__("Design Catalogue"),
			() => {
				frappe.route_options = { quality };
				frappe.set_route("design-catalogue");
			},
			__("View")
		);
	},
});
