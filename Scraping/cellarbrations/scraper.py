import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sync_playwright = None
    PlaywrightTimeout = Exception

from .config import BASE, CATEGORY_URL, USER_AGENT, MAX_LISTING_PAGES
from .js import (
    PRICE_EXTRACTOR_JS,
    DESCRIPTION_EXTRACTOR_JS,
    EXPAND_DESCRIPTION_JS,
    COLLECT_CARDS_JS,
    CLICK_NEXT_JS,
)
from .helpers import (
    parse_size,
    description_from_text,
    product_id_from_url,
    is_cloudflare_page,
    wait_out_cloudflare,
    scroll_listing,
    merge_items,
    PRODUCT_NUMBER_RE,
)


def collect_all_listing_products(page):
    by_url = {}
    stagnant = 0

    for page_no in range(1, MAX_LISTING_PAGES + 1):
        scroll_listing(page)
        batch = page.evaluate(COLLECT_CARDS_JS) or []
        added = merge_items(by_url, batch)
        print(
            f"  Listing page {page_no}: +{added} new "
            f"(batch={len(batch)}, total unique={len(by_url)})",
            file=sys.stderr,
        )

        stagnant = stagnant + 1 if added == 0 else 0
        if stagnant >= 2:
            print("  No new products on consecutive pages; stopping.", file=sys.stderr)
            break

        before_url = page.url
        clicked = page.evaluate(CLICK_NEXT_JS)
        if not clicked:
            scroll_listing(page, passes=8)
            batch2 = page.evaluate(COLLECT_CARDS_JS) or []
            added2 = merge_items(by_url, batch2)
            print(
                f"  Extra scroll (no Next): +{added2} new (total unique={len(by_url)})",
                file=sys.stderr,
            )
            break

        page.wait_for_timeout(2500)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(1500)

        if is_cloudflare_page(page) and not wait_out_cloudflare(page, "listing next"):
            print("  Cloudflare on next page; stopping pagination.", file=sys.stderr)
            break

        if page.url == before_url and added == 0:
            print("  Pagination did not advance; stopping.", file=sys.stderr)
            break

    return list(by_url.values())


def extract_description(page):
    for _ in range(3):
        try:
            clicked = page.evaluate(EXPAND_DESCRIPTION_JS)
        except Exception:
            clicked = False
        page.wait_for_timeout(700 if clicked else 300)
        if not clicked:
            break

    try:
        page.wait_for_function(
            """() => {
              const t = document.body ? document.body.innerText : '';
              return /Description/i.test(t) && t.length > 400;
            }""",
            timeout=12000,
        )
    except PlaywrightTimeout:
        pass

    page.wait_for_timeout(500)

    try:
        body_text = page.inner_text("body")
    except Exception:
        body_text = ""

    description = description_from_text(body_text)

    if not description:
        try:
            description = (
                page.evaluate(
                    "() => { const extractDescription = "
                    + DESCRIPTION_EXTRACTOR_JS
                    + "; return extractDescription(document); }"
                )
                or ""
            )
        except Exception:
            description = ""

    if not description:
        try:
            page.evaluate(EXPAND_DESCRIPTION_JS)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        try:
            body_text = page.inner_text("body")
        except Exception:
            body_text = ""
        description = description_from_text(body_text)
        if not description:
            try:
                description = (
                    page.evaluate(
                        "() => { const extractDescription = "
                        + DESCRIPTION_EXTRACTOR_JS
                        + "; return extractDescription(document); }"
                    )
                    or ""
                )
            except Exception:
                description = ""

    return (description or "").strip()


def scrape_with_playwright(max_products=None, fetch_descriptions=True, headless=True):
    if sync_playwright is None:
        raise RuntimeError(
            "playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-AU",
            viewport={"width": 1280, "height": 900},
            timezone_id="Australia/Sydney",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        print(f"Navigating to category: {CATEGORY_URL}", file=sys.stderr)
        try:
            page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(5000)
        except PlaywrightTimeout:
            print("Timeout loading category page", file=sys.stderr)
            browser.close()
            return rows

        if not wait_out_cloudflare(page, "category"):
            print(
                "Cloudflare did not clear. Re-run with --headed on a home network.",
                file=sys.stderr,
            )
            browser.close()
            return rows

        print(f"Page title: {page.title()!r}", file=sys.stderr)

        products = collect_all_listing_products(page)
        print(f"Found {len(products)} unique products across listing pages", file=sys.stderr)

        if max_products is not None:
            products = products[:max_products]
            print(f"Limited to --max-products {max_products}", file=sys.stderr)

        for i, item in enumerate(products):
            name = item.get("name") or ""
            url = item.get("url") or ""
            if url and not url.startswith("http"):
                url = urljoin(BASE, url)
            product_id = str(item.get("product_id") or "") or product_id_from_url(url)
            image = item.get("image") or ""
            price = item.get("price") or ""
            description = ""
            size_hint = name

            if url:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2000)

                    if is_cloudflare_page(page):
                        if not wait_out_cloudflare(page, f"product {i + 1}"):
                            print(f"  [{i + 1}] Cloudflare blocked product page: {url}", file=sys.stderr)
                            mu, un = parse_size(name)
                            rows.append({
                                "product_name": name,
                                "product_id": product_id,
                                "image": json.dumps([image] if image else []),
                                "url": url,
                                "price": price,
                                "scraped_at": scraped_at,
                                "description": "",
                                "measuring_unit": mu,
                                "units": un,
                            })
                            continue

                    try:
                        page.wait_for_function(
                            "() => document.body && ("
                            "document.body.innerText.includes('Product Number') || "
                            "document.body.innerText.includes('Description'))",
                            timeout=25000,
                        )
                    except PlaywrightTimeout:
                        print(f"  [{i + 1}] Product content slow/missing: {url}", file=sys.stderr)

                    page.wait_for_timeout(1000)

                    extra = page.evaluate(
                        "() => { const extractPrice = "
                        + PRICE_EXTRACTOR_JS
                        + "; "
                        + r"""
                        const sizeEl = document.querySelector(
                          '[class*="size"], [class*="volume"], [class*="pack"]'
                        );
                        return {
                          price: extractPrice(document),
                          size: sizeEl ? sizeEl.textContent.trim() : ''
                        };
                        }"""
                    )
                    if extra and extra.get("price") and not price:
                        price = extra["price"]
                    if extra and extra.get("size"):
                        size_hint = extra["size"]

                    if fetch_descriptions:
                        description = extract_description(page)
                        if not description:
                            dump = Path("debug_product_page.html")
                            dump.write_text(page.content(), encoding="utf-8")
                            print(f"  [{i + 1}] Description empty — saved {dump}", file=sys.stderr)

                    body_text = page.inner_text("body")
                    m = PRODUCT_NUMBER_RE.search(body_text or "")
                    if m:
                        product_id = m.group(1)
                    elif not product_id:
                        product_id = product_id_from_url(page.url) or product_id

                except Exception as e:
                    print(f"  [{i + 1}] product page failed for {url}: {e}", file=sys.stderr)
                    size_hint = name

            measuring_unit, units = parse_size(size_hint)
            if not measuring_unit:
                measuring_unit, units = parse_size(name)

            rows.append({
                "product_name": name,
                "product_id": product_id,
                "image": json.dumps([image] if image else []),
                "url": url,
                "price": price,
                "scraped_at": scraped_at,
                "description": description,
                "measuring_unit": measuring_unit,
                "units": units,
            })

            preview = (description[:45] + "...") if description else "(empty)"
            print(
                f"  [{i + 1}/{len(products)}] {name[:45]!r} "
                f"id={product_id} desc={preview!r}",
                file=sys.stderr,
            )

        browser.close()

    return rows