import csv
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from .config import BASE, SCROLLS_PER_PAGE

ID_URL_RE = re.compile(r"-id-(\d+)(?:[/?#]|$)", re.IGNORECASE)
PRODUCT_NUMBER_RE = re.compile(r"Product\s+Number\s*:\s*(\d+)", re.IGNORECASE)
SIZE_RE = re.compile(
    r"(?P<units>\d+(?:\.\d+)?)\s*(?P<unit>m[lL]|[lL]|ml|ML|g|kg|cl|oz)\b",
    re.IGNORECASE,
)


def parse_size(text):
    if not text:
        return "", ""
    m = SIZE_RE.search(text)
    if not m:
        return "", ""
    units = m.group("units")
    unit = m.group("unit")
    unit_norm = unit[0].upper() + unit[1:].lower() if len(unit) > 1 else unit.upper()
    if unit_norm.lower() in ("ml", "ml."):
        unit_norm = "Ml"
    elif unit_norm.lower() == "l":
        unit_norm = "L"
    return unit_norm, units


def description_from_text(body_text):
    if not body_text:
        return ""

    pn = PRODUCT_NUMBER_RE.search(body_text)
    head = body_text[: pn.start()] if pn else body_text

    ends = [m.end() for m in re.finditer(r"^\s*Description\s*$", head, re.I | re.M)]
    if not ends:
        m = re.search(r"(?i)Description\s*\n+", head)
        if m:
            ends = [m.end()]
    if not ends:
        return ""

    chunk = head[ends[-1]:]
    lines = []
    for ln in chunk.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if re.match(r"^(hide|show)\s+full\s+description$", ln, re.I):
            continue
        if re.match(r"^product\s+number\s*:", ln, re.I):
            break
        lines.append(ln)

    return " ".join(lines).strip()[:2000]


def product_id_from_url(url):
    m = ID_URL_RE.search(url or "")
    return m.group(1) if m else ""


def is_cloudflare_page(page):
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    try:
        body = page.inner_text("body")[:800].lower()
    except Exception:
        body = ""
    return (
        "checking your browser" in body
        or "verify you are human" in body
        or "cf-browser-verification" in body
    )


def wait_out_cloudflare(page, label="page"):
    for i in range(20):
        if not is_cloudflare_page(page):
            return True
        print(f"  Waiting for Cloudflare on {label}... ({(i + 1) * 3}s)", file=sys.stderr)
        page.wait_for_timeout(3000)
    return not is_cloudflare_page(page)


def scroll_listing(page, passes=SCROLLS_PER_PAGE):
    last_height = 0
    for _ in range(passes):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(900)
        try:
            height = page.evaluate("document.body.scrollHeight")
        except Exception:
            height = last_height
        if height == last_height:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            try:
                height = page.evaluate("document.body.scrollHeight")
            except Exception:
                break
            if height == last_height:
                break
        last_height = height


def merge_items(by_url, items):
    added = 0
    for item in items:
        url = item.get("url") or ""
        if url and not url.startswith("http"):
            url = urljoin(BASE, url)
            item = {**item, "url": url}
        if not url:
            continue
        if url not in by_url:
            by_url[url] = item
            added += 1
        else:
            prev = by_url[url]
            for k in ("name", "price", "image", "product_id"):
                if not prev.get(k) and item.get(k):
                    prev[k] = item[k]
    return added


def write_csv(rows, path):
    fieldnames = [
        "product_name",
        "product_id",
        "image",
        "url",
        "price",
        "scraped_at",
        "description",
        "measuring_unit",
        "units",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)