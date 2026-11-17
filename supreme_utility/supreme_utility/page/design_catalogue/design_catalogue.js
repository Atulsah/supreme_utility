// Design Catalogue: pick item variants (Quality + Design No) and get a PDF catalogue,
// one design per page with the company logo and the item's brand logo.
(function () {

const DC_API = "supreme_utility.catalogue";

frappe.pages["design-catalogue"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Design Catalogue"),
		single_column: true,
	});
	wrapper.design_catalogue = new DesignCatalogue(page, wrapper);
};

frappe.pages["design-catalogue"].on_page_show = function (wrapper) {
	const dc = wrapper.design_catalogue;
	if (dc && frappe.route_options) {
		dc.apply_route_options(frappe.route_options);
		frappe.route_options = null;
	}
};

class DesignCatalogue {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.rows = [];
		this.selected = new Map(); // item_code -> row (kept across filter changes)
		this.only_selected = false;
		this.make_controls();
		this.make_body();
		this.bind_realtime();
		if (frappe.route_options) {
			this.apply_route_options(frappe.route_options);
			frappe.route_options = null;
		} else {
			this.load_qualities().then(() => this.refresh());
		}
	}

	make_controls() {
		const p = this.page;
		this.brand_field = p.add_field({
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
			change: () => {
				if (this._brand === this.brand_field.get_value()) return;
				this._brand = this.brand_field.get_value();
				this.quality_field.set_value("");
				this.load_qualities().then(() => this.refresh());
			},
		});
		this.quality_field = p.add_field({
			fieldname: "quality",
			label: __("Quality"),
			fieldtype: "Select",
			options: [""],
			change: () => this.refresh(),
		});
		this.search_field = p.add_field({
			fieldname: "search",
			label: __("Design No / Item"),
			fieldtype: "Data",
			change: () => this.refresh(),
		});
		this.image_field = p.add_field({
			fieldname: "only_with_image",
			label: __("Only with Image"),
			fieldtype: "Check",
			default: 1,
			change: () => this.refresh(),
		});

		p.set_primary_action(__("Make PDF"), () => this.make_pdf(), "file");
		p.add_inner_button(__("Select All Shown"), () => this.select_all(true));
		p.add_inner_button(__("Clear Selection"), () => this.clear_selection());
		p.add_menu_item(__("Catalogue Settings"), () => frappe.set_route("Form", "Catalogue Settings"));
	}

	make_body() {
		this.$body = $(`
			<div class="dc-wrap">
				<div class="dc-bar">
					<span class="dc-count text-muted"></span>
					<span class="dc-sel"></span>
					<a class="dc-toggle-sel"></a>
				</div>
				<div class="dc-grid"></div>
			</div>`).appendTo(this.page.main);
		this.$grid = this.$body.find(".dc-grid");
		this.$count = this.$body.find(".dc-count");
		this.$sel = this.$body.find(".dc-sel");
		this.$toggle = this.$body.find(".dc-toggle-sel");

		this.$grid.on("click", ".dc-card", (e) => {
			const code = $(e.currentTarget).attr("data-code");
			const row = this.rows.find((r) => r.item_code === code) || this.selected.get(code);
			if (!row) return;
			if (this.selected.has(code)) this.selected.delete(code);
			else this.selected.set(code, row);
			$(e.currentTarget).toggleClass("selected", this.selected.has(code));
			this.update_bar();
		});
		this.$grid.on("click", ".dc-open", (e) => {
			e.stopPropagation();
			frappe.set_route("Form", "Item", $(e.currentTarget).closest(".dc-card").attr("data-code"));
		});
		this.$toggle.on("click", () => {
			this.only_selected = !this.only_selected;
			this.render();
		});
	}

	apply_route_options(opts) {
		const brand = opts.brand || "";
		this._brand = brand;
		this.brand_field.set_value(brand);
		this.load_qualities().then(() => {
			if (opts.quality) this.quality_field.set_value(opts.quality);
			this.refresh();
		});
	}

	load_qualities() {
		return frappe
			.call(`${DC_API}.get_filter_options`, { brand: this.brand_field.get_value() || null })
			.then((r) => {
				const q = (r.message && r.message.qualities) || [];
				const options = [{ value: "", label: __("All Qualities") }].concat(
					q.map((d) => ({ value: d.value, label: `${d.label || d.value} (${d.designs})` }))
				);
				this.quality_field.df.options = options;
				this.quality_field.refresh();
			});
	}

	refresh() {
		const args = {
			brand: this.brand_field.get_value() || null,
			quality: this.quality_field.get_value() || null,
			txt: this.search_field.get_value() || null,
			only_with_image: this.image_field.get_value() ? 1 : 0,
		};
		const key = JSON.stringify(args);
		this._last = key;
		this.$grid.html(`<div class="text-muted dc-empty">${__("Loading...")}</div>`);
		frappe.call(`${DC_API}.get_items`, args).then((r) => {
			if (this._last !== key) return; // a newer filter is already loading
			this.rows = r.message || [];
			this.render();
		});
	}

	render() {
		const rows = this.only_selected ? Array.from(this.selected.values()) : this.rows;
		if (!rows.length) {
			this.$grid.html(
				`<div class="text-muted dc-empty">${
					this.only_selected ? __("Nothing selected yet.") : __("No item variants found for these filters.")
				}</div>`
			);
		} else {
			let last_quality = null;
			const html = [];
			rows.forEach((r) => {
				if (r.quality !== last_quality) {
					last_quality = r.quality;
					html.push(`<div class="dc-group">${frappe.utils.escape_html(r.quality || __("No Quality"))}</div>`);
				}
				const img = r.image
					? `<img loading="lazy" src="${encodeURI(r.image)}" alt="">`
					: `<div class="dc-noimg">${__("No Image")}</div>`;
				html.push(`
					<div class="dc-card ${this.selected.has(r.item_code) ? "selected" : ""}" data-code="${frappe.utils.escape_html(r.item_code)}" title="${frappe.utils.escape_html(r.item_code + " - " + (r.item_name || ""))}">
						<div class="dc-thumb">${img}<span class="dc-check">✓</span></div>
						<div class="dc-label">${frappe.utils.escape_html(r.label)}</div>
						<div class="dc-sub text-muted">${frappe.utils.escape_html([r.brand, r.item_code].filter(Boolean).join(" · "))}
							<a class="dc-open" title="${__("Open Item")}">↗</a></div>
					</div>`);
			});
			this.$grid.html(html.join(""));
		}
		this.$count.text(this.only_selected ? "" : __("{0} designs shown", [this.rows.length]));
		this.update_bar();
	}

	update_bar() {
		const n = this.selected.size;
		this.$sel.html(n ? `<b>${__("{0} selected", [n])}</b>` : "");
		this.$toggle.text(this.only_selected ? __("Show all") : n ? __("Show selected only") : "");
		this.page.set_primary_action(n ? __("Make PDF ({0})", [n]) : __("Make PDF"), () => this.make_pdf(), "file");
	}

	select_all(on) {
		const rows = this.only_selected ? Array.from(this.selected.values()) : this.rows;
		rows.forEach((r) => (on ? this.selected.set(r.item_code, r) : this.selected.delete(r.item_code)));
		this.render();
	}

	clear_selection() {
		this.selected.clear();
		this.only_selected = false;
		this.render();
	}

	make_pdf() {
		const items = Array.from(this.selected.values());
		if (!items.length) {
			frappe.msgprint(__("Please select the designs first (click on the pictures)."));
			return;
		}
		const no_image = items.filter((r) => !r.image).map((r) => r.label);
		const qualities = [...new Set(items.map((r) => r.quality).filter(Boolean))];
		const d = new frappe.ui.Dialog({
			title: __("Make Design Catalogue"),
			fields: [
				{
					fieldname: "title",
					fieldtype: "Data",
					label: __("Title"),
					default: __("Design Catalogue"),
					description: qualities.length ? __("Qualities: {0}", [qualities.join(", ")]) : "",
				},
				{ fieldname: "include_index", fieldtype: "Check", label: __("Add Design Index page at the end"), default: 1 },
				{
					fieldname: "info",
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("{0} designs, one per page.", [items.length])}${
						no_image.length
							? `<br><span class="text-warning">${__("No image for: {0}", [frappe.utils.escape_html(no_image.join(", "))])}</span>`
							: ""
					}</p>`,
				},
			],
			primary_action_label: __("Make PDF"),
			primary_action: (v) => {
				d.hide();
				frappe.call({
					method: `${DC_API}.generate_catalogue`,
					args: {
						item_codes: items.map((r) => r.item_code),
						title: v.title,
						include_index: v.include_index ? 1 : 0,
					},
					freeze: true,
					freeze_message: __("Making the catalogue..."),
				}).then((r) => {
					const m = r.message || {};
					if (m.queued) {
						frappe.msgprint({
							title: __("Catalogue is being made"),
							message: __("{0} designs - this takes a little while. You will get a notification with the download link.", [m.count]),
							indicator: "blue",
						});
					} else {
						this.show_result(m);
					}
				});
			},
		});
		d.show();
	}

	show_result(m) {
		if (m.error) {
			frappe.msgprint({ title: __("Design Catalogue"), message: m.error, indicator: "red" });
			return;
		}
		const missing = (m.missing_images || []).length
			? `<p class="text-warning">${__("No image for: {0}", [frappe.utils.escape_html(m.missing_images.join(", "))])}</p>`
			: "";
		frappe.msgprint({
			title: __("Catalogue Ready"),
			indicator: "green",
			message: `<p>${__("{0} designs.", [m.count])}</p>${missing}
				<p><a class="btn btn-primary btn-sm" href="${m.file_url}" target="_blank" rel="noopener">${__("Open PDF")}</a>
				<a class="btn btn-default btn-sm" href="${m.file_url}" download="${frappe.utils.escape_html(m.file_name)}">${__("Download")}</a></p>`,
		});
		window.open(m.file_url, "_blank");
	}

	bind_realtime() {
		if (frappe.pages["design-catalogue"]._realtime_bound) return;
		frappe.pages["design-catalogue"]._realtime_bound = true;
		frappe.realtime.on("design_catalogue_ready", (m) => {
			const dc = this.wrapper.design_catalogue || this;
			dc.show_result(m || {});
		});
	}
}

})();
