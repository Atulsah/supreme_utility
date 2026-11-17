# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Design Catalogue: pick designs from the Catalogue Design master and get a PDF catalogue.

Catalogue Design (one record per design) holds Design No, Image (upload or link), Brand,
Quality and Colour. It is not linked to Item.
  Brand logo  Catalogue Settings > Brand Logos (else Brand.image, else an image attached to the Brand)
  Company     Catalogue Settings > Company Logo, else the default Company's logo, else the app logo

Small catalogues are made right away; big ones go to a background job and the user gets a
notification with the download link when it is ready.
"""

import io
import json
import os
import re
import zipfile
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
	if not (ALLOWED_ROLES & set(frappe.get_roles())) or not frappe.has_permission("Catalogue Design", "read"):
		frappe.throw(_("You are not permitted to make design catalogues."), frappe.PermissionError)


def get_settings():
	if frappe.db.exists("DocType", "Catalogue Settings"):
		return frappe.get_cached_doc("Catalogue Settings")
	return frappe._dict()


def _load_designs(conditions=None, values=None, names=None):
	"""Active Catalogue Design rows with their label, sorted by quality then design no."""
	conditions = list(conditions or [])
	values = dict(values or {})
	conditions.append("disabled = 0")
	if names is not None:
		if not names:
			return []
		conditions.append("name in %(names)s")
		values["names"] = tuple(names)
	rows = frappe.db.sql(
		f"""
		select name, design_no, image, brand, quality, colour
		from `tabCatalogue Design`
		where {" and ".join(conditions)}
		limit {MAX_LIST}
		""",
		values,
		as_dict=True,
	)
	for r in rows:
		r.label = cr.design_label(r.design_no, [r.colour])
	rows.sort(key=lambda r: (cr.natural_key(r.quality), cr.natural_key(r.design_no), cr.natural_key(r.label)))
	return rows


HTTP_HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
	"Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,text/html;q=0.8,*/*;q=0.5",
}


def direct_image_url(url):
	"""Turn share links into direct image links (Google Drive, Dropbox)."""
	url = cstr(url).strip()
	m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=\w+&)?id=)([\w-]+)", url)
	if m:
		return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
	if "dropbox.com" in url:
		url = re.sub(r"([?&])dl=0", r"\1raw=1", url)
		if "raw=1" not in url:
			url += ("&" if "?" in url else "?") + "raw=1"
	return url


def _fetch_remote(url, _depth=0):
	"""Download an image link. If the link is a web page (postimg.cc, imgbb...), use its og:image."""
	import requests

	try:
		resp = requests.get(direct_image_url(url), headers=HTTP_HEADERS, timeout=20, allow_redirects=True)
	except Exception:
		return None
	if not resp.ok or not resp.content:
		return None
	if cr.open_image(resp.content):
		return resp.content
	ctype = resp.headers.get("Content-Type", "")
	if _depth == 0 and "html" in ctype:
		html = resp.text
		for pattern in (
			r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
			r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
			r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
			r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
		):
			m = re.search(pattern, html, re.I)
			if m:
				from urllib.parse import urljoin

				return _fetch_remote(urljoin(resp.url, m.group(1).replace("&amp;", "&")), _depth=1)
	return None


def _read_file(url):
	"""Bytes of an image given its URL (/files, /private/files, /assets or http)."""
	url = cstr(url).strip()
	if not url:
		return None
	site = get_url()
	if url.startswith(site):
		url = url[len(site) :]
	if url.startswith(("http://", "https://")):
		return _fetch_remote(url)

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


def _brand_logo(settings, brand):
	"""Catalogue Settings > Brand Logos, else Brand.image (if the field exists), else an image
	attached to the Brand."""
	urls = [row.logo for row in (settings.get("brand_logos") or []) if row.brand == brand and row.logo]
	if frappe.db.has_column("Brand", "image"):
		urls.append(frappe.db.get_value("Brand", brand, "image"))
	urls += frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Brand", "attached_to_name": brand, "is_folder": 0},
		pluck="file_url",
		order_by="creation desc",
	)
	for url in urls:
		img = _image(url) if url else None
		if img:
			return img
	return None


def _slug(text):
	text = re.sub(r"[^A-Za-z0-9]+", "-", cstr(text)).strip("-")
	return text[:60] or "Design-Catalogue"


def _parse_codes(values):
	if isinstance(values, str):
		values = json.loads(values)
	codes = []
	for c in values or []:
		c = cstr(c).strip()
		if c and c not in codes:
			codes.append(c)
	return codes


# ---------------------------------------------------------------- API for the page


@frappe.whitelist()
def get_filter_options(brand=None):
	"""Qualities (with design count) for the Quality filter."""
	check_access()
	values = {}
	brand_sql = ""
	if brand:
		brand_sql = "and brand = %(brand)s"
		values["brand"] = brand
	rows = frappe.db.sql(
		f"""
		select ifnull(quality, '') as value, count(*) as designs
		from `tabCatalogue Design`
		where disabled = 0 {brand_sql}
		group by ifnull(quality, '')
		""",
		values,
		as_dict=True,
	)
	rows = [r for r in rows if r.value]
	rows.sort(key=lambda r: cr.natural_key(r.value))
	return {"qualities": [{"value": r.value, "label": r.value, "designs": r.designs} for r in rows]}


@frappe.whitelist()
def get_items(brand=None, quality=None, txt=None, only_with_image=0):
	"""Designs for the selection grid."""
	check_access()
	conditions, values = [], {}
	if brand:
		conditions.append("brand = %(brand)s")
		values["brand"] = brand
	if quality:
		conditions.append("quality = %(quality)s")
		values["quality"] = quality
	if cint(only_with_image):
		conditions.append("ifnull(image, '') != ''")
	rows = _load_designs(conditions, values)
	if txt:
		t = txt.lower().strip()
		rows = [r for r in rows if t in cstr(r.label).lower() or t in cstr(r.quality).lower()]
	return [
		{
			"name": r.name,
			"image": r.image,
			"brand": r.brand,
			"quality": r.quality,
			"design_no": r.design_no,
			"label": r.label,
		}
		for r in rows
	]


@frappe.whitelist()
def generate_catalogue(designs, title=None, include_index=1):
	"""Make the PDF now, or in the background for big selections. designs = Catalogue Design names."""
	check_access()
	names = _parse_codes(designs)
	if not names:
		frappe.throw(_("Please select at least one design."))
	if len(names) > MAX_ITEMS:
		frappe.throw(_("Please select at most {0} designs in one catalogue.").format(MAX_ITEMS))

	settings = get_settings()
	background_after = cint(settings.get("background_after")) or DEFAULT_BACKGROUND_AFTER
	if len(names) > background_after:
		frappe.enqueue(
			"supreme_utility.catalogue.build_in_background",
			queue="long",
			timeout=3600,
			names=names,
			title=title,
			include_index=cint(include_index),
			user=frappe.session.user,
		)
		return {"queued": 1, "count": len(names)}

	return build_catalogue(names, title=title, include_index=cint(include_index))


def build_in_background(names, title=None, include_index=1, user=None):
	try:
		result = build_catalogue(names, title=title, include_index=include_index)
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


def build_catalogue(names, title=None, include_index=1):
	"""Builds the PDF, saves it as a private File and returns its URL."""
	settings = get_settings()
	rows = _load_designs(names=names)
	if not rows:
		frappe.throw(_("None of the selected designs is active."))

	designs = [
		{
			"label": r.label,
			"quality": r.quality,
			"brand": r.brand,
			"item_code": r.name,
			"image": _image(r.image),
		}
		for r in rows
	]

	brand_logos = {}
	for r in rows:
		if r.brand and r.brand not in brand_logos:
			brand_logos[r.brand] = _brand_logo(settings, r.brand)

	qualities = []
	for r in rows:
		if r.quality and r.quality not in qualities:
			qualities.append(r.quality)
	title = cstr(title).strip() or settings.get("catalogue_title") or "Design Catalogue"

	pdf = cr.render_pdf(
		designs,
		company_logo=_company_logo(settings),
		brand_logos=brand_logos,
		title=title,
		subtitle=settings.get("subtitle") or "",
		footer=settings.get("footer_text") or "",
		primary_color=settings.get("primary_color") or "#E3171B",
		secondary_color=settings.get("secondary_color") or "#0038AB",
		include_index=bool(cint(include_index)),
	)

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
		"missing_brand_logos": [b for b, img in brand_logos.items() if not img],
	}


# ---------------------------------------------------------------- ZIP import

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


@frappe.whitelist()
def import_designs_from_zip(zip_file, brand=None, quality=None, update_existing=0):
	"""One Catalogue Design per picture in the ZIP; the file name is the Design No."""
	from supreme_utility.supreme_utility.doctype.catalogue_design.catalogue_design import design_name

	if not frappe.has_permission("Catalogue Design", "create"):
		frappe.throw(_("You are not permitted to create Catalogue Designs."), frappe.PermissionError)
	content = _read_file(zip_file)
	if not content:
		frappe.throw(_("Could not read the ZIP file."))
	try:
		archive = zipfile.ZipFile(io.BytesIO(content))
	except zipfile.BadZipFile:
		frappe.throw(_("This is not a ZIP file."))

	quality = cstr(quality).strip() or None
	result = {"created": 0, "updated": 0, "skipped": 0, "not_images": []}
	for info in archive.infolist():
		path = info.filename
		base = os.path.basename(path)
		if info.is_dir() or not base or base.startswith(".") or "__MACOSX" in path:
			continue
		stem, ext = os.path.splitext(base)
		if ext.lower() not in IMAGE_EXTENSIONS:
			result["not_images"].append(base)
			continue
		data = archive.read(info)
		if not cr.open_image(data):
			result["not_images"].append(base)
			continue
		design_no = stem.strip()
		name = design_name(quality, design_no)
		exists = frappe.db.exists("Catalogue Design", name)
		if exists and not cint(update_existing):
			result["skipped"] += 1
			continue

		file_doc = frappe.get_doc({"doctype": "File", "file_name": base, "is_private": 0, "content": data})
		file_doc.insert(ignore_permissions=True)
		if exists:
			doc = frappe.get_doc("Catalogue Design", name)
			doc.image = file_doc.file_url
			if brand:
				doc.brand = brand
			doc.save()
			result["updated"] += 1
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Catalogue Design",
					"design_no": design_no,
					"quality": quality,
					"brand": brand or None,
					"image": file_doc.file_url,
				}
			).insert()
			result["created"] += 1
		file_doc.db_set(
			{
				"attached_to_doctype": "Catalogue Design",
				"attached_to_name": doc.name,
				"attached_to_field": "image",
			}
		)
	frappe.db.commit()
	return result


# ---------------------------------------------------------------- thumbnails for the page


THUMB_CACHE_SECONDS = 24 * 60 * 60


@frappe.whitelist()
def design_thumb(name, v=None):
	"""Small JPEG of a Catalogue Design image, whatever kind of link it is (cached for a day)."""
	check_access()
	url = frappe.db.get_value("Catalogue Design", name, "image")
	if not url:
		raise frappe.DoesNotExistError
	key = f"catalogue_thumb::{url}"
	data = frappe.cache().get_value(key)
	if not data:
		img = _image(url)
		if not img:
			raise frappe.DoesNotExistError
		img = img.copy()
		img.thumbnail((480, 480))
		if img.mode != "RGB":
			img = img.convert("RGB")
		buf = io.BytesIO()
		img.save(buf, "JPEG", quality=80)
		data = buf.getvalue()
		frappe.cache().set_value(key, data, expires_in_sec=THUMB_CACHE_SECONDS)
	frappe.local.response.filename = "thumb.jpg"
	frappe.local.response.filecontent = data
	frappe.local.response.type = "download"
	frappe.local.response.display_content_as = "inline"
