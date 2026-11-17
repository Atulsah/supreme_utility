# Copyright (c) 2026, SGPL and contributors
# For license information, please see license.txt

"""Design Catalogue: draws the catalogue pages with Pillow and joins them into one PDF.

No wkhtmltopdf: every page is drawn as a picture at a fixed size, so the layout is the same on
every server. No Frappe imports, so the layout can be tested on its own.

Layout (A4 portrait, one design per page):
  cover   company logo, brand logo(s), title, sample strip
  design  header (company logo left, brand logo right), big image, "DESIGN NO." footer
  index   all designs as small thumbnails with their design numbers
"""

import io
import os
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(APP_DIR, "public", "fonts")

DPI = 170  # sharp on phones and fine to print; ~0.4-0.7 MB per page
JPEG_QUALITY = 85

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

INK = (27, 31, 42)
MUTED = (107, 112, 128)
FAINT = (154, 158, 170)
RULE = (228, 224, 216)
SHADOW = (227, 225, 220)
NOIMG = (201, 196, 186)


# ---------------------------------------------------------------- small helpers


def mm(v: float) -> int:
	return round(v / 25.4 * DPI)


def pt(v: float) -> int:
	return round(v / 72 * DPI)


def hex_rgb(value: str, default=(0, 0, 0)):
	value = (value or "").strip().lstrip("#")
	if len(value) == 3:
		value = "".join(c * 2 for c in value)
	try:
		return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
	except Exception:
		return default


def open_image(content: bytes) -> Image.Image | None:
	try:
		img = Image.open(io.BytesIO(content))
		img = ImageOps.exif_transpose(img)
		img.load()
		return img
	except Exception:
		return None


def contain(w, h, box_w, box_h):
	"""Fit (w, h) inside the box, centred. Returns left, top, width, height."""
	s = min(box_w / w, box_h / h)
	dw, dh = w * s, h * s
	return (box_w - dw) / 2, (box_h - dh) / 2, dw, dh


def cover(w, h, box_w, box_h):
	"""Fill the box (cropping the overflow), centred. Returns left, top, width, height."""
	s = max(box_w / w, box_h / h)
	dw, dh = w * s, h * s
	return (box_w - dw) / 2, (box_h - dh) / 2, dw, dh


def natural_key(value) -> tuple:
	"""'149 Red' < '163' < '1001' - numbers compare as numbers."""
	parts = re.split(r"(\d+)", str(value or "").strip().lower())
	return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p != "")


def design_label(design_no: str, extras: list[str]) -> str:
	extras = [e for e in extras if e]
	return f"{design_no} ({', '.join(extras)})" if extras else str(design_no)


_font_cache = {}


def font(weight: str, size_pt: float):
	key = (weight, size_pt)
	if key not in _font_cache:
		path = os.path.join(FONT_DIR, f"Poppins-{weight}.ttf")
		try:
			_font_cache[key] = ImageFont.truetype(path, pt(size_pt))
		except Exception:
			_font_cache[key] = ImageFont.load_default(size=pt(size_pt))
	return _font_cache[key]


def fit_font(draw, text, weight, size_pt, max_w_px, min_pt=10):
	"""Largest font (up to size_pt) at which text fits in max_w_px."""
	size = size_pt
	while size > min_pt:
		f = font(weight, size)
		if draw.textlength(text, font=f) <= max_w_px:
			return f
		size -= 1
	return font(weight, min_pt)


def flat(img: Image.Image, bg=(255, 255, 255)) -> Image.Image:
	"""RGB copy with any transparency put on a white background."""
	if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
		img = img.convert("RGBA")
		base = Image.new("RGB", img.size, bg)
		base.paste(img, mask=img.split()[3])
		return base
	return img.convert("RGB")


def resized(img, w, h):
	return flat(img).resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)


def rounded_mask(w, h, r):
	m = Image.new("L", (w, h), 0)
	ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
	return m


# ---------------------------------------------------------------- drawing blocks


class Style:
	def __init__(self, primary, secondary, footer_text):
		self.primary = hex_rgb(primary, (227, 23, 27))
		self.secondary = hex_rgb(secondary, (0, 56, 171))
		self.footer_text = footer_text or ""


def new_page():
	page = Image.new("RGB", (mm(PAGE_W), mm(PAGE_H)), "white")
	return page, ImageDraw.Draw(page)


def stripes(draw, st, h_mm=2.0):
	w, H, h = mm(PAGE_W), mm(PAGE_H), mm(h_mm)
	for y0 in (0, H - h):
		draw.rectangle([0, y0, w // 2, y0 + h], fill=st.primary)
		draw.rectangle([w // 2, y0, w, y0 + h], fill=st.secondary)


def paste_logo(page, img, x_mm, y_mm, max_w_mm, max_h_mm, align="left"):
	"""Logo fitted in the box (vertically centred)."""
	if not img:
		return
	_l, _t, w, h = contain(img.width, img.height, mm(max_w_mm), mm(max_h_mm))
	logo = img.convert("RGBA").resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
	x = mm(x_mm)
	if align == "right":
		x -= logo.width
	elif align == "center":
		x -= logo.width // 2
	y = mm(y_mm) + (mm(max_h_mm) - logo.height) // 2
	page.paste(logo, (x, y), logo)


def header(page, draw, st, company_logo, brand_logo, brand_name):
	stripes(draw, st)
	paste_logo(page, company_logo, MARGIN, 5, 40, 17)
	if brand_logo:
		paste_logo(page, brand_logo, PAGE_W - MARGIN, 6, 52, 15, align="right")
	elif brand_name:
		draw.text(
			(mm(PAGE_W - MARGIN), mm(13.5)), brand_name, font=font("Bold", 15), fill=st.secondary, anchor="rm"
		)
	draw.line([mm(MARGIN), mm(HEADER_H), mm(PAGE_W - MARGIN), mm(HEADER_H)], fill=RULE, width=max(1, mm(0.3)))


def footer_text(draw, st, y_mm=3.0):
	if st.footer_text:
		draw.text(
			(mm(PAGE_W / 2), mm(PAGE_H - y_mm)),
			st.footer_text.replace("\n", "  •  "),
			font=font("Regular", 7),
			fill=FAINT,
			anchor="md",
		)


def picture(page, draw, img, x, y, w, h, radius_mm=1.5):
	"""img fitted (contain) into the box x, y, w, h (px), rounded corners and a soft shadow."""
	left, top, dw, dh = contain(img.width, img.height, w, h)
	x0, y0 = round(x + left), round(y + top)
	pic = resized(img, dw, dh)
	r = mm(radius_mm)
	off = mm(0.9)
	draw.rounded_rectangle(
		[x0 + off, y0 + round(off * 1.4), x0 + pic.width + off, y0 + pic.height + round(off * 1.4)],
		radius=r,
		fill=SHADOW,
	)
	page.paste(pic, (x0, y0), rounded_mask(pic.width, pic.height, r))


def tile(page, img, x, y, w, h, radius_mm=1.0):
	"""img cropped (cover) to exactly fill the box."""
	left, top, dw, dh = cover(img.width, img.height, w, h)
	pic = resized(img, dw, dh)
	cx, cy = round(-left), round(-top)
	pic = pic.crop((cx, cy, cx + round(w), cy + round(h)))
	page.paste(pic, (round(x), round(y)), rounded_mask(pic.width, pic.height, mm(radius_mm)))


def empty_box(draw, x, y, w, h, text, size=11):
	draw.rounded_rectangle([x, y, x + w, y + h], radius=mm(2), outline=NOIMG, width=max(1, mm(0.4)))
	draw.text((x + w / 2, y + h / 2), text, font=font("Regular", size), fill=FAINT, anchor="mm")


# ---------------------------------------------------------------- pages


def cover_page(st, title, subtitle, qualities, total, company_logo, brand_logos, strip_images):
	page, draw = new_page()
	stripes(draw, st, 3.5)
	paste_logo(page, company_logo, PAGE_W / 2, 38, 75, 62, align="center")

	logos = [img for img in brand_logos if img]
	if logos:
		box_w, box_h = (85, 32) if len(logos) == 1 else (58, 24)
		widths = [contain(i.width, i.height, box_w, box_h)[2] for i in logos]
		gap = 8
		x = (PAGE_W - sum(widths) - gap * (len(logos) - 1)) / 2
		for img, w in zip(logos, widths, strict=True):
			paste_logo(page, img, x, 110, w, box_h)
			x += w + gap

	y = 160
	head = title.upper()
	draw.text(
		(mm(PAGE_W / 2), mm(y)), head, font=fit_font(draw, head, "Bold", 27, mm(180)), fill=INK, anchor="mm"
	)
	if qualities and " ".join(qualities).lower() != title.lower():
		y += 13
		text = "  •  ".join(qualities)
		draw.text(
			(mm(PAGE_W / 2), mm(y)),
			text,
			font=fit_font(draw, text, "Medium", 15, mm(180)),
			fill=INK,
			anchor="mm",
		)
	y += 10
	sub = (f"{subtitle}  •  " if subtitle else "") + f"{total} Design{'s' if total != 1 else ''}"
	draw.text((mm(PAGE_W / 2), mm(y)), sub, font=font("Light", 12), fill=MUTED, anchor="mm")

	if strip_images:
		n = len(strip_images)
		cell_w = (PAGE_W - 2 * 22 - (n - 1) * 3) / n
		cell_h = cell_w * 1.55
		x = (PAGE_W - cell_w * n - 3 * (n - 1)) / 2
		for img in strip_images:
			tile(page, img, mm(x), mm(205), mm(cell_w), mm(cell_h))
			x += cell_w + 3

	footer_text(draw, st, 7)
	return page


def design_page(st, d, no, total, company_logo, brand_logo):
	page, draw = new_page()
	header(page, draw, st, company_logo, brand_logo, d.get("brand"))

	x, y = mm(MARGIN), mm(HEADER_H + IMG_GAP)
	w, h = mm(IMG_BOX_W), mm(IMG_BOX_H)
	if d.get("image"):
		picture(page, draw, d["image"], x, y, w, h)
	else:
		empty_box(draw, x + mm(30), y + mm(60), w - mm(60), mm(60), "Image not available")

	draw.text((mm(MARGIN), mm(PAGE_H - 19)), "DESIGN NO.", font=font("Medium", 8.5), fill=MUTED, anchor="ls")
	label = d["label"]
	draw.text(
		(mm(MARGIN), mm(PAGE_H - 7)),
		label,
		font=fit_font(draw, label, "Bold", 30, mm(125)),
		fill=st.primary,
		anchor="ls",
	)
	meta = "  •  ".join([v for v in (d.get("brand"), d.get("quality")) if v])
	right = mm(PAGE_W - MARGIN)
	if meta:
		draw.text((right, mm(PAGE_H - 14)), meta, font=font("Medium", 8.5), fill=INK, anchor="rs")
	draw.text(
		(right, mm(PAGE_H - 9)), f"Page {no} / {total}", font=font("Regular", 8.5), fill=MUTED, anchor="rs"
	)
	footer_text(draw, st)
	stripes(draw, st)
	return page


def index_pages(st, designs, company_logo, brand_logo):
	cell_w = (PAGE_W - 2 * MARGIN - (INDEX_COLS - 1) * INDEX_GAP) / INDEX_COLS
	avail_h = PAGE_H - INDEX_TOP - INDEX_BOTTOM
	cell_h = (avail_h - (INDEX_ROWS - 1) * INDEX_GAP) / INDEX_ROWS - INDEX_LABEL_H
	per_page = INDEX_COLS * INDEX_ROWS
	chunks = [designs[i : i + per_page] for i in range(0, len(designs), per_page)]
	for n, chunk in enumerate(chunks, 1):
		page, draw = new_page()
		header(page, draw, st, company_logo, brand_logo, None)
		title_font = font("Bold", 15)
		draw.text((mm(MARGIN), mm(HEADER_H + 10)), "Design Index", font=title_font, fill=INK, anchor="ls")
		if len(chunks) > 1:
			tx = mm(MARGIN) + draw.textlength("Design Index", font=title_font) + mm(2)
			draw.text(
				(tx, mm(HEADER_H + 10)),
				f"({n}/{len(chunks)})",
				font=font("Light", 11),
				fill=MUTED,
				anchor="ls",
			)
		for k, d in enumerate(chunk):
			r, c = divmod(k, INDEX_COLS)
			x = MARGIN + c * (cell_w + INDEX_GAP)
			y = INDEX_TOP + r * (cell_h + INDEX_LABEL_H + INDEX_GAP)
			if d.get("image"):
				tile(page, d["image"], mm(x), mm(y), mm(cell_w), mm(cell_h))
			else:
				empty_box(draw, mm(x), mm(y), mm(cell_w), mm(cell_h), "No image", 7)
			label = d["label"]
			draw.text(
				(mm(x + cell_w / 2), mm(y + cell_h + 4.5)),
				label,
				font=fit_font(draw, label, "Medium", 8, mm(cell_w), 5),
				fill=INK,
				anchor="ms",
			)
		stripes(draw, st)
		yield page


# ---------------------------------------------------------------- whole catalogue


def page_pdf(page: Image.Image) -> bytes:
	buf = io.BytesIO()
	page.save(buf, "PDF", resolution=DPI, quality=JPEG_QUALITY)
	return buf.getvalue()


def render_pdf(
	designs: list[dict],
	company_logo: Image.Image | None,
	brand_logos: dict,
	title: str,
	subtitle: str = "",
	footer: str = "",
	primary_color: str = "#E3171B",
	secondary_color: str = "#0038AB",
	include_index: bool = True,
) -> bytes:
	"""designs: [{"label", "quality", "brand", "image": PIL.Image|None}], already sorted.
	brand_logos: {brand name: PIL.Image|None}. Pages are made one at a time to keep memory low.
	"""
	from pypdf import PdfReader, PdfWriter

	st = Style(primary_color, secondary_color, footer)
	writer = PdfWriter()

	def add(page):
		for p in PdfReader(io.BytesIO(page_pdf(page))).pages:
			writer.add_page(p)

	qualities, brands = [], []
	for d in designs:
		if d.get("quality") and d["quality"] not in qualities:
			qualities.append(d["quality"])
		if d.get("brand") and d["brand"] not in brands:
			brands.append(d["brand"])

	with_img = [d["image"] for d in designs if d.get("image")]
	strip = []
	if with_img:
		n = min(6, len(with_img))
		step = len(with_img) / n
		strip = [with_img[int(k * step)] for k in range(n)]
	cover_logos = [brand_logos.get(b) for b in brands]
	add(cover_page(st, title, subtitle, qualities, len(designs), company_logo, cover_logos, strip))

	for no, d in enumerate(designs, 1):
		add(design_page(st, d, no, len(designs), company_logo, brand_logos.get(d.get("brand"))))

	if include_index and len(designs) > 1:
		index_logo = brand_logos.get(brands[0]) if len(brands) == 1 else None
		for page in index_pages(st, designs, company_logo, index_logo):
			add(page)

	writer.add_metadata({"/Title": title, "/Creator": "Supreme Utility - Design Catalogue"})
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()
