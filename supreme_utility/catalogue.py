# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Design Catalogue: pick item variants (Quality + Design No) and get a PDF catalogue.

Where the data comes from
  Quality    the variant's template item (Item.variant_of -> item_name), or an Item Attribute
             (Catalogue Settings > Quality From)
  Design No  the variant's design attribute (Catalogue Settings > Design Attribute; if empty, the
             first attribute whose name contains "Design" or "Model")
  Image      Item.image of the variant
  Brand      Item.brand of the variant (else of its template); the logo is Brand.image
  Company    Catalogue Settings > Company Logo, else the default Company's logo, else the app logo

Small catalogues are made right away; big ones go to a background job and the user gets a
notification with the download link when it is ready.
"""

import json
import os
import re
from urllib.parse import unquote

import frappe
from frappe.utils import cint, cstr, get_url, nowdate

from supreme_utility import catalogue_render as cr
from supreme_utility.messages import _

ALLOWED_ROLES = {
	"Sales User",
	"Sales Manager",
	"Stock User",
	"Stock Manager",
	"Item Manager",
	"System Manager",
}
MAX_ITEMS = 500
MAX_LIST = 2000
DEFAULT_BACKGROUND_AFTER = 40
APP_LOGO = "/assets/supreme_utility/images/Supreme.png"
REALTIME_EVENT = "design_catalogue_ready"


# ---------------------------------------------------------------- helpers


def check_access():
	if frappe.session.user == "Administrator":
		return
	if not (ALLOWED_ROLES & set(frappe.get_roles())) or not frappe.has_permission("Item", "read"):
		frappe.throw(_("You are not permitted to make design catalogues."), frappe.PermissionError)


def get_settings():
	if frappe.db.exists("DocType", "Catalogue Settings"):
		return frappe.get_cached_doc("Catalogue Settings")
	return frappe._dict()


def _design_attribute(settings, attribute_names):
	if settings.get("design_attribute"):
		return settings.design_attribute
	for pattern in ("design", "model"):
		for name in attribute_names:
			if pattern in (name or "").lower():
				return name
	return None


def _quality_from_attribute(settings):
	return settings.get("quality_from") == "Item Attribute" and settings.get("quality_attribute")


def _load_variants(filters_sql="", values=None, item_codes=None):
	"""Variant rows with quality, design no and label, sorted by quality then design no."""
	settings = get_settings()
	values = dict(values or {})
	conditions = ["v.variant_of is not null", "v.variant_of != ''", "v.disabled = 0"]
	if item_codes is not None:
		if not item_codes:
			return []
		conditions.append("v.name in %(item_codes)s")
		values["item_codes"] = tuple(item_codes)
	if filters_sql:
		conditions.append(filters_sql)

	rows = frappe.db.sql(
		f"""
		select v.name as item_code, v.item_name, v.image,
			coalesce(nullif(v.brand, ''), t.brand) as brand,
			v.variant_of as template, t.item_name as template_name
		from `tabItem` v
		left join `tabItem` t on t.name = v.variant_of
		where {" and ".join(conditions)}
		limit {MAX_LIST}
		""",
		values,
		as_dict=True,
	)
	if not rows:
		return []

	attrs = {}
	for a in frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ("in", [r.item_code for r in rows]), "parenttype": "Item"},
		fields=["parent", "attribute", "attribute_value", "idx"],
		order_by="idx asc",
	):
		attrs.setdefault(a.parent, []).append(a)

	all_attr_names = {a.attribute for lst in attrs.values() for a in lst}
	design_attr = _design_attribute(settings, sorted(all_attr_names))
	quality_attr = _quality_from_attribute(settings)
	show_others = cint(settings.get("show_other_attributes", 1)) if settings else 1

	out = []
	for r in rows:
		lst = attrs.get(r.item_code, [])
		by_name = {a.attribute: cstr(a.attribute_value) for a in lst}
		design_no = by_name.get(design_attr) if design_attr else None
		quality = by_name.get(quality_attr) if quality_attr else None
		quality = quality or r.template_name or r.template
		extras = []
		if show_others:
			extras = [
				cstr(a.attribute_value)
				for a in lst
				if a.attribute not in (design_attr, quality_attr) and a.attribute_value
			]
		r.quality = quality
		r.design_no = design_no or r.item_code
		r.extras = extras
		r.label = cr.design_label(r.design_no, extras)
		out.append(r)

	out.sort(key=lambda r: (cr.natural_key(r.quality), cr.natural_key(r.design_no), cr.natural_key(r.label)))
	return out


def _read_file(url):
	"""Bytes of an image given its URL (/files, /private/files, /assets or http)."""
	url = cstr(url).strip()
	if not url:
		return None
	site = get_url()
	if url.startswith(site):
		url = url[len(site) :]
	if url.startswith(("http://", "https://")):
		try:
			import requests

			resp = requests.get(url, timeout=15)
			return resp.content if resp.ok else None
		except Exception:
			return None

	url = url.split("?")[0]
	name = frappe.db.get_value("File", {"file_url": url}, "name")
	if name:
		try:
			return frappe.get_doc("File", name).get_content()
		except Exception:
			pass

	path = unquote(url)
	if path.startswith("/private/files/"):
		path = frappe.get_site_path(path.lstrip("/"))
	elif path.startswith("/files/"):
		path = frappe.get_site_path("public", path.lstrip("/"))
	elif path.startswith("/assets/supreme_utility/"):
		path = os.path.join(cr.APP_DIR, "public", path[len("/assets/supreme_utility/") :])
	elif path.startswith("/assets/"):
		path = os.path.join(frappe.local.sites_path, path.lstrip("/"))
	else:
		return None
	if os.path.exists(path):
		with open(path, "rb") as f:
			return f.read()
	return None


def _image(url):
	content = _read_file(url)
	return cr.open_image(content) if content else None


def _company_logo(settings):
	urls = [settings.get("company_logo")]
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if company and frappe.db.has_column("Company", "company_logo"):
		urls.append(frappe.db.get_value("Company", company, "company_logo"))
	urls.append(APP_LOGO)
	for url in urls:
		img = _image(url) if url else None
		if img:
			return img
	return None


def _slug(text):
	text = re.sub(r"[^A-Za-z0-9]+", "-", cstr(text)).strip("-")
	return text[:60] or "Design-Catalogue"


def _parse_codes(item_codes):
	if isinstance(item_codes, str):
		item_codes = json.loads(item_codes)
	codes = []
	for c in item_codes or []:
		c = cstr(c).strip()
		if c and c not in codes:
			codes.append(c)
	return codes


# ---------------------------------------------------------------- API for the page


@frappe.whitelist()
def get_filter_options(brand=None):
	"""Qualities (with design count) for the Quality filter."""
	check_access()
	settings = get_settings()
	values = {}
	brand_sql = ""
	if brand:
		brand_sql = "and coalesce(nullif(v.brand, ''), t.brand) = %(brand)s"
		values["brand"] = brand

	quality_attr = _quality_from_attribute(settings)
	if quality_attr:
		values["qa"] = quality_attr
		rows = frappe.db.sql(
			f"""
			select a.attribute_value as value, a.attribute_value as label, count(*) as designs
			from `tabItem` v
			left join `tabItem` t on t.name = v.variant_of
			join `tabItem Variant Attribute` a on a.parent = v.name and a.parenttype = 'Item' and a.attribute = %(qa)s
			where v.variant_of is not null and v.variant_of != '' and v.disabled = 0 {brand_sql}
			group by a.attribute_value
			""",
			values,
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			f"""
			select v.variant_of as value, max(t.item_name) as label, count(*) as designs
			from `tabItem` v
			left join `tabItem` t on t.name = v.variant_of
			where v.variant_of is not null and v.variant_of != '' and v.disabled = 0 {brand_sql}
			group by v.variant_of
			""",
			values,
			as_dict=True,
		)
	rows.sort(key=lambda r: cr.natural_key(r.label or r.value))
	return {"qualities": rows, "quality_from": "Item Attribute" if quality_attr else "Template Item"}


@frappe.whitelist()
def get_items(brand=None, quality=None, txt=None, only_with_image=0):
	"""Variants for the selection grid."""
	check_access()
	settings = get_settings()
	conditions, values = [], {}
	if brand:
		conditions.append("coalesce(nullif(v.brand, ''), t.brand) = %(brand)s")
		values["brand"] = brand
	if quality:
		quality_attr = _quality_from_attribute(settings)
		if quality_attr:
			conditions.append(
				"exists (select 1 from `tabItem Variant Attribute` qa where qa.parent = v.name "
				"and qa.parenttype = 'Item' and qa.attribute = %(qa)s and qa.attribute_value = %(quality)s)"
			)
			values["qa"] = quality_attr
		else:
			conditions.append("v.variant_of = %(quality)s")
		values["quality"] = quality
	if cint(only_with_image):
		conditions.append("ifnull(v.image, '') != ''")

	rows = _load_variants(" and ".join(conditions), values)

	if txt:
		t = txt.lower().strip()
		rows = [
			r
			for r in rows
			if t in cstr(r.label).lower()
			or t in cstr(r.item_code).lower()
			or t in cstr(r.item_name).lower()
			or t in cstr(r.quality).lower()
		]

	return [
		{
			"item_code": r.item_code,
			"item_name": r.item_name,
			"image": r.image,
			"brand": r.brand,
			"quality": r.quality,
			"design_no": r.design_no,
			"label": r.label,
		}
		for r in rows
	]


@frappe.whitelist()
def generate_catalogue(item_codes, title=None, include_index=1):
	"""Make the PDF now, or in the background for big selections."""
	check_access()
	codes = _parse_codes(item_codes)
	if not codes:
		frappe.throw(_("Please select at least one design."))
	if len(codes) > MAX_ITEMS:
		frappe.throw(_("Please select at most {0} designs in one catalogue.").format(MAX_ITEMS))

	settings = get_settings()
	background_after = cint(settings.get("background_after")) or DEFAULT_BACKGROUND_AFTER
	if len(codes) > background_after:
		frappe.enqueue(
			"supreme_utility.catalogue.build_in_background",
			queue="long",
			timeout=3600,
			item_codes=codes,
			title=title,
			include_index=cint(include_index),
			user=frappe.session.user,
		)
		return {"queued": 1, "count": len(codes)}

	return build_catalogue(codes, title=title, include_index=cint(include_index))


def build_in_background(item_codes, title=None, include_index=1, user=None):
	try:
		result = build_catalogue(item_codes, title=title, include_index=include_index)
		frappe.publish_realtime(REALTIME_EVENT, result, user=user)
		_notify(user, result)
	except Exception:
		frappe.log_error(title="Design Catalogue failed")
		frappe.publish_realtime(
			REALTIME_EVENT, {"error": _("The catalogue could not be made. See Error Log.")}, user=user
		)


def _notify(user, result):
	if not user or not frappe.db.exists("DocType", "Notification Log"):
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": _("Design Catalogue ready: {0}").format(result.get("file_name")),
				"email_content": f'<a href="{result.get("file_url")}">{_("Download PDF")}</a>',
				"document_type": "File",
				"document_name": result.get("file"),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Design Catalogue notification failed")


def build_catalogue(item_codes, title=None, include_index=1):
	"""Builds the PDF, saves it as a private File and returns its URL."""
	from frappe.utils.pdf import get_pdf

	settings = get_settings()
	rows = _load_variants(item_codes=item_codes)
	if not rows:
		frappe.throw(_("None of the selected items is an active item variant."))

	designs = [
		{
			"label": r.label,
			"quality": r.quality,
			"brand": r.brand,
			"item_code": r.item_code,
			"image": _image(r.image),
		}
		for r in rows
	]

	brand_logos = {}
	for r in rows:
		if r.brand and r.brand not in brand_logos:
			brand_logos[r.brand] = _image(frappe.db.get_value("Brand", r.brand, "image"))

	qualities = []
	for r in rows:
		if r.quality and r.quality not in qualities:
			qualities.append(r.quality)
	title = cstr(title).strip() or settings.get("catalogue_title") or "Design Catalogue"

	context = cr.build_context(
		designs,
		company_logo=_company_logo(settings),
		brand_logos=brand_logos,
		title=title,
		subtitle=settings.get("subtitle") or "",
		footer_text=settings.get("footer_text") or "",
		primary_color=settings.get("primary_color") or "#E3171B",
		secondary_color=settings.get("secondary_color") or "#0038AB",
		include_index=bool(cint(include_index)),
	)
	html = cr.render_html(context)
	pdf = get_pdf(html, options=dict(cr.PDF_OPTIONS))

	name_part = "-".join(qualities[:3]) if qualities else title
	file_name = f"{_slug(name_part)}-Catalogue-{nowdate()}.pdf"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"is_private": 1,
			"content": pdf,
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert()
	frappe.db.commit()
	return {
		"file": file_doc.name,
		"file_url": file_doc.file_url,
		"file_name": file_doc.file_name,
		"count": len(designs),
		"missing_images": [d["label"] for d in designs if not d["image"]],
	}
