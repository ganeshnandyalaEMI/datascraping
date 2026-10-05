PRICE_EXTRACTOR_JS = r"""(root) => {
  const clean = v => (v || '').toString().trim().replace(/\s+/g, ' ');
  const pricePattern = /(?:AU\$|A\$|\$)\s*\d[\d,]*(?:\.\d{1,2})?|\b\d+\.\d{2}\b/;

  const fromValue = value => {
    const m = clean(value).match(pricePattern);
    return m ? m[0].replace(/\s+/g, '') : '';
  };

  if (root === document) {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const visit = value => {
          if (Array.isArray(value)) {
            for (const item of value) {
              const r = visit(item);
              if (r) return r;
            }
          } else if (value && typeof value === 'object') {
            if (value['@graph']) {
              const r = visit(value['@graph']);
              if (r) return r;
            }
            const offers = Array.isArray(value.offers) ? value.offers : [value.offers];
            for (const offer of offers) {
              if (offer && offer.price != null) {
                const r = fromValue(offer.price);
                if (r) return r;
              }
            }
            for (const child of Object.values(value)) {
              if (child && typeof child === 'object') {
                const r = visit(child);
                if (r) return r;
              }
            }
          }
          return '';
        };
        const result = visit(JSON.parse(script.textContent || ''));
        if (result) return result;
      } catch (_) {}
    }
  }

  const selectors = [
    '[itemprop="price"]',
    'meta[property="product:price:amount"]',
    'meta[name="price"]',
    '[data-testid*="price" i]',
    '[class*="price" i]',
    '[aria-label*="price" i]'
  ];
  for (const el of root.querySelectorAll(selectors.join(','))) {
    if (el.closest('[hidden], [aria-hidden="true"]')) continue;
    const result = fromValue(
      el.getAttribute('content')
      || el.getAttribute('data-price')
      || el.getAttribute('aria-label')
      || el.innerText
      || el.textContent
    );
    if (result) return result;
  }
  return '';
}"""


DESCRIPTION_EXTRACTOR_JS = r"""(root) => {
  const clean = value => {
    const div = document.createElement('div');
    div.innerHTML = value || '';
    return (div.textContent || '').trim().replace(/\s+/g, ' ');
  };

  const isNoise = line =>
    !line
    || /^description$/i.test(line)
    || /^(hide|show)\s+full\s+description$/i.test(line)
    || /^open\s+product\s+description$/i.test(line)
    || /^product\s+number\s*:/i.test(line)
    || /^alcohol\s+volume$/i.test(line)
    || /^pack\s+format$/i.test(line);

  const headings = [...root.querySelectorAll('h1,h2,h3,h4,h5,h6,strong,b,p,div,span,button')]
    .filter(el => /^description$/i.test((el.innerText || el.textContent || '').trim()));

  for (const heading of headings) {
    let container = heading;
    for (let depth = 0; depth < 8 && container; depth++, container = container.parentElement) {
      const lines = (container.innerText || container.textContent || '')
        .split(/\n+/)
        .map(l => l.trim().replace(/\s+/g, ' '))
        .filter(l => !isNoise(l));
      const text = lines.join(' ').trim();
      if (text.length >= 30) return text.slice(0, 2000);
    }
  }

  for (const script of root.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const findDesc = value => {
        if (Array.isArray(value)) {
          for (const item of value) {
            const r = findDesc(item);
            if (r) return r;
          }
        } else if (value && typeof value === 'object') {
          const types = Array.isArray(value['@type']) ? value['@type'] : [value['@type']];
          if (types.some(t => /product/i.test(t || '')) && value.description) {
            const r = clean(value.description);
            if (r) return r;
          }
          if (value['@graph']) {
            const r = findDesc(value['@graph']);
            if (r) return r;
          }
          for (const child of Object.values(value)) {
            if (child && typeof child === 'object') {
              const r = findDesc(child);
              if (r) return r;
            }
          }
        }
        return '';
      };
      const result = findDesc(JSON.parse(script.textContent || ''));
      if (result) return result.slice(0, 2000);
    } catch (_) {}
  }

  const selectors = [
    '[itemprop="description"]',
    '[data-testid*="description" i]',
    '[class*="product-description" i]',
    '[id*="product-description" i]',
    '[class*="description" i]',
    '[id*="description" i]'
  ];
  for (const el of root.querySelectorAll(selectors.join(','))) {
    if (el.matches('button, a, summary, [role="button"]')) continue;
    if (el.closest('button, a, summary, [role="button"], [hidden], [aria-hidden="true"]')) continue;
    const text = clean(el.innerHTML);
    if (text && text.length >= 30 && !/^(open|show)\s+/i.test(text)) {
      return text.slice(0, 2000);
    }
  }

  for (const sel of ['meta[name="description"]', 'meta[property="og:description"]']) {
    const text = clean(root.querySelector(sel)?.getAttribute('content') || '');
    if (text && text.length >= 20) return text.slice(0, 2000);
  }
  return '';
}"""


EXPAND_DESCRIPTION_JS = r"""() => {
  const nodes = [...document.querySelectorAll('button, summary, a, [role="button"], span, div')];
  const trigger = nodes.find(el => {
    const t = (el.innerText || el.textContent || '').trim();
    if (t.length > 80) return false;
    return /open\s+product\s+description|show\s+full\s+description|read\s+more|view\s+more|see\s+more/i.test(t)
      && !/hide\s+full\s+description/i.test(t);
  });
  if (trigger) {
    trigger.scrollIntoView({ block: 'center' });
    trigger.click();
    return true;
  }
  return false;
}"""


COLLECT_CARDS_JS = (
    "() => { const extractPrice = "
    + PRICE_EXTRACTOR_JS
    + "; "
    + r"""
    const items = [];
    const cards = document.querySelectorAll(
      '[data-testid*="product"], [class*="product-card"], [class*="ProductCard"], article, .product, [class*="tile"]'
    );
    const seen = new Set();

    for (const card of cards) {
      const link = card.querySelector('a[href*="/product"], a[href*="/products"], a[href*="pid"]')
        || card.closest('a')
        || card.querySelector('a');
      if (!link) continue;

      const href = link.href || link.getAttribute('href') || '';
      if (!href || seen.has(href)) continue;
      if (/\/(categories|cart|account|login)/i.test(href)) continue;
      seen.add(href);

      const nameEl = card.querySelector('h2, h3, [class*="name"], [class*="title"], [data-testid*="name"]');
      const imgEl = card.querySelector('img');
      const name = (nameEl && nameEl.textContent || link.textContent || '').trim().replace(/\s+/g, ' ');
      const price = extractPrice(card);
      const image = imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '';

      let pid = card.getAttribute('data-product-id') || card.getAttribute('data-id') || '';
      if (!pid) {
        const m = href.match(/-id-(\d+)(?:[\/?#]|$)/i)
          || href.match(/(?:product[_-]?id|pid)=([\w-]+)/i);
        if (m) pid = m[1];
      }

      if (name || href) {
        items.push({ name, price, image, url: href, product_id: pid });
      }
    }

    if (items.length < 3) {
      document.querySelectorAll('a[href*="product"]').forEach(a => {
        const href = a.href;
        if (seen.has(href)) return;
        seen.add(href);
        const name = (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200);
        if (name.length > 5) {
          const m = href.match(/-id-(\d+)(?:[\/?#]|$)/i);
          items.push({ name, price: '', image: '', url: href, product_id: m ? m[1] : '' });
        }
      });
    }
    return items;
    }"""
)


CLICK_NEXT_JS = r"""() => {
  const candidates = [
    ...document.querySelectorAll(
      'a[rel="next"], button[rel="next"], [aria-label*="next" i], [aria-label*="Next page" i], [class*="pagination"] a, [class*="Pagination"] a, nav a, [data-testid*="next" i]'
    )
  ];
  for (const el of candidates) {
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
    if (/next|>|»/i.test(t) || el.getAttribute('rel') === 'next') {
      el.click();
      return true;
    }
  }

  const pages = [...document.querySelectorAll(
    '[class*="pagination"] a, [class*="Pagination"] a, nav[aria-label*="pagination" i] a'
  )];
  let activeIdx = -1;
  for (let i = 0; i < pages.length; i++) {
    const p = pages[i];
    const cls = (p.className || '').toString();
    const aria = p.getAttribute('aria-current') || '';
    if (/active|current|selected/i.test(cls) || aria === 'page') {
      activeIdx = i;
      break;
    }
  }
  if (activeIdx >= 0 && activeIdx + 1 < pages.length) {
    pages[activeIdx + 1].click();
    return true;
  }
  return false;
}"""