# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Design Catalogue: turns a list of designs into the catalogue HTML (then PDF).

This module has no Frappe imports on purpose, so the layout can be tested on its own.
Data loading lives in supreme_utility.catalogue.

Layout (A4 portrait, one design per page):
  cover   company logo, brand logo(s), title, sample strip
  design  header (company logo left, brand logo right), big image, "DESIGN NO." footer
  index   all designs as small thumbnails with their design numbers
"""

import base64
import io
import os
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(APP_DIR, "templates", "catalogue")
FONT_DIR = os.path.join(APP_DIR, "public", "fonts")
FONTS = {"Light": 300, "Regular": 400, "Medium": 500, "Bold": 700}

# Page geometry in mm (A4 = 210 x 297)
PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 13.0
HEADER_H = 28.0
FOOTER_H = 34.0
IMG_GAP = 5.0
IMG_BOX_W = PAGE_W - 2 * MARGIN
IMG_BOX_H = PAGE_H - HEADER_H - FOOTER_H - 2 * IMG_GAP

INDEX_COLS, INDEX_ROWS = 6, 4
INDEX_GAP = 3.5
INDEX_LABEL_H = 6.0
INDEX_TOP = HEADER_H + 16.0
INDEX_BOTTOM = 14.0

# Image quality of the PDF: long side in pixels, JPEG quality
DESIGN_PX, THUMB_PX, LOGO_PX = 1600, 500, 600
JPEG_QUALITY = 84

PDF_OPTIONS = {
	"page-size": "A4",
	"margin-top": "0mm",
	"margin-bottom": "0mm",
	"margin-left": "0mm",
	"margin-right": "0mm",
	"disable-smart-shrinking": "",
}


# ---------------------------------------------------------------- images


def open_image(content: bytes) -> Image.Image | None:
	try:
		img = Image.open(io.BytesIO(content))
		img = ImageOps.exif_transpose(img)
		img.load()
		return img
	except Exception:
		return None


def to_data_uri(img: Image.Image, max_px: int, keep_alpha: bool = False) -> str:
	"""Shrink to max_px on the long side and return a data: URI (JPEG, or PNG for logos)."""
	img = img.copy()
	img.thumbnail((max_px, max_px), Image.LANCZOS)
	buf = io.BytesIO()
	if keep_alpha:
		if img.mode not in ("RGBA", "LA", "P"):
			img = img.convert("RGB")
		img.save(buf, "PNG", optimize=True)
		mime = "image/png"
	else:
		if img.mode in ("RGBA", "LA", "P"):
			img = img.convert("RGBA")
			bg = Image.new("RGB", img.size, "white")
			bg.paste(img, mask=img.split()[3])
			img = bg
		elif img.mode != "RGB":
			img = img.convert("RGB")
		img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
		mime = "image/jpeg"
	return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def contain(w, h, box_w, box_h):
	"""Fit (w, h) inside the box, centred. Returns left, top, width, height in mm."""
	s = min(box_w / w, box_h / h)
	dw, dh = w * s, h * s
	return round((box_w - dw) / 2, 2), round((box_h - dh) / 2, 2), round(dw, 2), round(dh, 2)


def cover(w, h, box_w, box_h):
	"""Fill the box (cropping the overflow), centred. Returns left, top, width, height in mm."""
	s = max(box_w / w, box_h / h)
	dw, dh = w * s, h * s
	return round((box_w - dw) / 2, 2), round((box_h - dh) / 2, 2), round(dw, 2), round(dh, 2)


def logo_box(img: Image.Image | None, max_w: float, max_h: float):
	if not img:
		return None
	_l, _t, w, h = contain(img.width, img.height, max_w, max_h)
	return {"src": to_data_uri(img, LOGO_PX, keep_alpha=True), "w": w, "h": h}


# ---------------------------------------------------------------- sorting / labels


def natural_key(value) -> tuple:
	"""'149 Red' < '163' < '1001' - numbers compare as numbers."""
	parts = re.split(r"(\d+)", str(value or "").strip().lower())
	return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p != "")


def design_label(design_no: str, extras: list[str]) -> str:
	extras = [e for e in extras if e]
	return f"{design_no} ({', '.join(extras)})" if extras else str(design_no)


# ---------------------------------------------------------------- fonts


def font_faces() -> str:
	css = []
	for name, weight in FONTS.items():
		path = os.path.join(FONT_DIR, f"Poppins-{name}.ttf")
		if not os.path.exists(path):
			continue
		with open(path, "rb") as f:
			b64 = base64.b64encode(f.read()).decode()
		css.append(
			"@font-face{font-family:'CatPoppins';font-weight:%d;font-style:normal;"
			"src:url(data:font/truetype;charset=utf-8;base64,%s) format('truetype');}" % (weight, b64)
		)
	return "\n".join(css)


# ---------------------------------------------------------------- context + html


def build_context(
	designs: list[dict],
	company_logo: Image.Image | None,
	brand_logos: dict,
	title: str,
	subtitle: str = "",
	footer_text: str = "",
	primary_color: str = "#E3171B",
	secondary_color: str = "#0038AB",
	include_index: bool = True,
) -> dict:
	"""designs: [{"label", "quality", "brand", "image": PIL.Image|None, "item_code"}], already sorted.
	brand_logos: {brand name: PIL.Image|None}
	"""
	header_company = logo_box(company_logo, 40, 17)
	header_brands = {b: logo_box(img, 52, 15) for b, img in brand_logos.items()}
	cover_company = logo_box(company_logo, 75, 62)
	cover_brands = [logo_box(img, 85, 32) for img in brand_logos.values() if img]
	cover_brands = [b for b in cover_brands if b]
	if len(cover_brands) > 1:
		cover_brands = [logo_box(img, 58, 24) for img in brand_logos.values() if img]

	total = len(designs)
	pages = []
	thumbs = []
	for i, d in enumerate(designs, 1):
		img = d.get("image")
		page = {
			"no": i,
			"label": d["label"],
			"quality": d.get("quality") or "",
			"brand": d.get("brand") or "",
			"item_code": d.get("item_code") or "",
			"brand_logo": header_brands.get(d.get("brand")),
			"img": None,
		}
		if img:
			left, top, w, h = contain(img.width, img.height, IMG_BOX_W, IMG_BOX_H)
			page["img"] = {"src": to_data_uri(img, DESIGN_PX), "left": left, "top": top, "w": w, "h": h}
		pages.append(page)
		thumbs.append({"label": d["label"], "image": img})

	# cover strip: up to 6 designs spread across the list
	strip = []
	with_img = [t for t in thumbs if t["image"]]
	if with_img:
		n = min(6, len(with_img))
		step = len(with_img) / n
		cell_w = (PAGE_W - 2 * 22 - (n - 1) * 3) / n
		cell_h = cell_w * 1.55
		for k in range(n):
			img = with_img[int(k * step)]["image"]
			left, top, w, h = cover(img.width, img.height, cell_w, cell_h)
			strip.append(
				{
					"src": to_data_uri(img, THUMB_PX),
					"left": left,
					"top": top,
					"w": w,
					"h": h,
					"cw": cell_w,
					"ch": cell_h,
				}
			)

	index_pages = []
	if include_index and total > 1:
		cell_w = (PAGE_W - 2 * MARGIN - (INDEX_COLS - 1) * INDEX_GAP) / INDEX_COLS
		avail_h = PAGE_H - INDEX_TOP - INDEX_BOTTOM
		cell_h = (avail_h - (INDEX_ROWS - 1) * INDEX_GAP) / INDEX_ROWS - INDEX_LABEL_H
		per_page = INDEX_COLS * INDEX_ROWS
		for start in range(0, total, per_page):
			rows = []
			chunk = thumbs[start : start + per_page]
			for r in range(0, len(chunk), INDEX_COLS):
				row = []
				for t in chunk[r : r + INDEX_COLS]:
					cell = {"label": t["label"], "img": None}
					if t["image"]:
						left, top, w, h = cover(t["image"].width, t["image"].height, cell_w, cell_h)
						cell["img"] = {
							"src": to_data_uri(t["image"], THUMB_PX),
							"left": left,
							"top": top,
							"w": w,
							"h": h,
						}
					row.append(cell)
				rows.append(row)
			index_pages.append(rows)
		index_cell = {"w": round(cell_w, 2), "h": round(cell_h, 2)}
	else:
		index_cell = None

	qualities = []
	for d in designs:
		if d.get("quality") and d["quality"] not in qualities:
			qualities.append(d["quality"])
	brands = []
	for d in designs:
		if d.get("brand") and d["brand"] not in brands:
			brands.append(d["brand"])

	return {
		"title": title,
		"subtitle": subtitle,
		"footer_text": footer_text,
		"primary": primary_color or "#E3171B",
		"secondary": secondary_color or "#0038AB",
		"fonts_css": font_faces(),
		"header_company": header_company,
		"cover_company": cover_company,
		"cover_brands": cover_brands,
		"strip": strip,
		"pages": pages,
		"total": total,
		"index_pages": index_pages,
		"index_cell": index_cell,
		"index_gap": INDEX_GAP,
		"qualities": qualities,
		"brands": brands,
		"g": {
			"page_w": PAGE_W,
			"page_h": PAGE_H,
			"margin": MARGIN,
			"header_h": HEADER_H,
			"footer_h": FOOTER_H,
			"img_top": HEADER_H + IMG_GAP,
			"img_w": IMG_BOX_W,
			"img_h": IMG_BOX_H,
			"index_top": INDEX_TOP,
			"label_h": INDEX_LABEL_H,
		},
	}


def render_html(context: dict) -> str:
	env = Environment(
		loader=FileSystemLoader(TEMPLATE_DIR),
		autoescape=select_autoescape(["html"]),
		trim_blocks=True,
		lstrip_blocks=True,
	)
	return env.get_template("design_catalogue.html").render(**context)
