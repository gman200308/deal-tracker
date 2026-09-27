"""Readers for stores that aren't on Shopify.

Each reader returns products shaped like Shopify's products.json (id, title, handle, url,
product_type, body_html, images, variants[id, title, price, compare_at_price, available]),
so the rest of the tracker treats every store the same way.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import Callable

import requests

log = logging.getLogger("deal_tracker")


# --- Protein à Rabais (proteinarabais.com, custom CMS) --------------------------------
# Listing pages: /categorie/<slug>[/page/N] show title + "from" price per product.
# Product pages embed every variant in JS arrays (g_arr_variant, g_arr_price_selling, ...)
# with the options joined by \x01, e.g. "Boite (12 barres)\x01Caramel Cashew (exp 05/2027)".

PAR_DEFAULT_CATEGORIES = ["liquidation", "barres-de-proteines", "chocolat", "epicerie-et-collations"]
# "epicerie-et-collations" is mostly groceries (pasta, sweeteners, flour), so items from it
# must pass on title keywords alone.
PAR_SNACK_CATEGORIES = {"barres-de-proteines", "chocolat"}
_PAR_CARD = re.compile(r"location\.assign\('(/[^'#]+)#produit'\).*?txt_pr_title_list\"[^>]*>([^<]+)<", re.S)


def _js_array(page: str, name: str) -> list[str]:
    m = re.search(rf"{name}\s*=\s*new Array\((.*?)\);", page, re.S)
    return [html.unescape(v) for v in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))] if m else []


def _ld_product(page: str) -> dict:
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def _to_float(v: str) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def fetch_proteinarabais(session: requests.Session, store: dict, cfg: dict,
                         keep: Callable[[str, str], bool]) -> list[dict]:
    """keep(title, product_type) decides which listings are worth opening, so we only
    fetch product pages for snacks (a few dozen requests, not the whole store)."""
    p = cfg["polling"]
    base = store["base_url"].rstrip("/")
    delay = p["delay_between_requests_seconds"]

    listing: dict[str, tuple[str, str]] = {}   # slug -> (title, product_type)
    for cat in store.get("categories") or PAR_DEFAULT_CATEGORIES:
        ptype = "snacks" if cat in PAR_SNACK_CATEGORIES else ""
        seen: set[str] = set()
        for page in range(1, p["max_pages"] + 1):
            url = f"{base}/categorie/{cat}" + (f"/page/{page}" if page > 1 else "")
            r = session.get(url, timeout=p["request_timeout_seconds"])
            r.raise_for_status()
            r.encoding = "utf-8"
            cards = [(slug, html.unescape(t).strip()) for slug, t in _PAR_CARD.findall(r.text)]
            new = [c for c in cards if c[0] not in seen]
            if not new:            # past the last page (the site repeats or returns nothing)
                break
            for slug, title in new:
                seen.add(slug)
                old = listing.get(slug)
                listing[slug] = (title, ptype or (old[1] if old else ""))
            time.sleep(delay)
    if not listing:
        raise RuntimeError("no products found on listing pages -- site layout changed?")

    products = []
    for slug, (title, ptype) in listing.items():
        if not keep(title, ptype):
            continue
        r = session.get(base + slug, timeout=p["request_timeout_seconds"])
        time.sleep(delay)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        r.encoding = "utf-8"
        page = r.text
        names = _js_array(page, "g_arr_variant")
        selling = _js_array(page, "g_arr_price_selling")
        regular = _js_array(page, "g_arr_price_regular")
        other = _js_array(page, "g_arr_price_other")
        backorder = _js_array(page, "g_arr_backorder")
        codes = _js_array(page, "g_arr_product_code")
        pid = re.search(r"g_int_product_id\s*=\s*(\d+)", page)
        if not names or not pid:
            log.warning("Protein à Rabais: couldn't read variants on %s", slug)
            continue
        ld = _ld_product(page)
        images = ld.get("image") or []
        variants = []
        for i, name in enumerate(names):
            price = _to_float(selling[i] if i < len(selling) else "")
            if price is None:
                continue
            refs = [_to_float(x[i]) for x in (regular, other) if i < len(x)]
            compare = max([x for x in refs if x] or [0]) or None
            variants.append({
                "id": codes[i] if i < len(codes) and codes[i] else f"{pid.group(1)}-{i}",
                "title": " / ".join(part.strip() for part in name.split("\x01") if part.strip()) or "Default Title",
                "price": f"{price:.2f}",
                "compare_at_price": f"{compare:.2f}" if compare and compare > price else None,
                # "0" = orderable; "1" = the page shows "Temporairement non disponible"
                "available": (backorder[i] if i < len(backorder) else "1") == "0",
            })
        products.append({
            "id": int(pid.group(1)),
            "title": title,
            "handle": slug.strip("/"),
            "url": base + slug,
            "product_type": ptype,
            "body_html": ld.get("description", ""),
            "images": [{"src": images[0] if isinstance(images, list) else images}] if images else [],
            "variants": variants,
        })
    return products


# --- Well.ca (custom platform) ------------------------------------------------------------
# Category pages are server-rendered, 32 products per page, and each card carries a
# productList.add({...}) JSON blob (name, id, price, category) plus the regular/sale price,
# pack size and a Sold Out button. Pagination is broken on the site itself: past page 2
# ("?page=2&infinitescroll=true") it repeats earlier products, for real visitors too. Page 1
# of each sort order reaches further, so we take the union. That covers most of a category,
# and we log how much.

WELL_DEFAULT_CATEGORIES = ["fitness-protein-clearance_6664", "protein-bars-snacks_1106"]
WELL_SORTS = ["price_asc", "price_desc", "name_asc", "name_desc", "date_desc", "date_asc",
              "popularity_desc", "rating_desc"]
_WELL_CARD_SPLIT = re.compile(r'(?=<div class="product-item product-id-\d+")')
_MONEY = re.compile(r"\$\s*([\d,]+\.\d{2})")


def _well_card(card: str) -> dict | None:
    blob = re.search(r"productList\.add\((\{.*?\})\s*,", card, re.S)
    href = re.search(r'<a href="(https://well\.ca/products/[^"?]+)', card)
    if not blob or not href:
        return None
    try:
        info = json.loads(blob.group(1))
    except json.JSONDecodeError:
        return None
    name = html.unescape(info.get("name", "")).strip()
    size = re.search(r'class="product_grid_info_subtitle">\s*([^<]*)<', card)
    orig = re.search(r'class="product_grid_original_price">([^<]*)<', card)
    new = re.search(r'class="product_grid_new_price">([^<]*)<', card)
    price = float(info["price"]) if info.get("price") is not None else None
    if new and _MONEY.search(new.group(1)):
        price = float(_MONEY.search(new.group(1)).group(1).replace(",", ""))
    if price is None:
        return None
    compare = None
    if orig and _MONEY.search(orig.group(1)):
        compare = float(_MONEY.search(orig.group(1)).group(1).replace(",", ""))
    img = re.search(r'<img src="([^"]+)"', card)
    size_txt = html.unescape(size.group(1)).strip() if size else ""
    return {
        "id": int(info["id"]),
        # Pack size ("12 x 44 g") goes in the title so the count parser can read it.
        "title": f"{name} ({size_txt})" if size_txt else name,
        "handle": href.group(1).rsplit("/", 1)[-1],
        "url": href.group(1),
        "product_type": html.unescape(info.get("category", "")).replace("\\/", "/"),
        "body_html": "",
        "images": [{"src": img.group(1)}] if img else [],
        "variants": [{
            "id": int(info["id"]),
            "title": "Default Title",
            "price": f"{price:.2f}",
            "compare_at_price": f"{compare:.2f}" if compare and compare > price else None,
            "available": "btn-soldout" not in card,
        }],
    }


def fetch_wellca(session: requests.Session, store: dict, cfg: dict,
                 keep: Callable[[str, str], bool]) -> list[dict]:
    p = cfg["polling"]
    base = store["base_url"].rstrip("/")
    products: dict[int, dict] = {}
    for cat in store.get("categories") or WELL_DEFAULT_CATEGORIES:
        url = f"{base}/categories/{cat}.html"
        pages = [url, f"{url}?page=2&infinitescroll=true"] + [f"{url}?sort={s}" for s in WELL_SORTS]
        total = None
        seen: set[int] = set()
        for u in pages:
            r = session.get(u, timeout=p["request_timeout_seconds"])
            r.raise_for_status()
            if total is None:
                m = re.search(r'data-total="(\d+)"', r.text)
                total = int(m.group(1)) if m else None
            for card in _WELL_CARD_SPLIT.split(r.text)[1:]:
                prod = _well_card(card)
                if prod:
                    seen.add(prod["id"])
                    products.setdefault(prod["id"], prod)
            time.sleep(p["delay_between_requests_seconds"])
            if total and len(seen) >= total:
                break
        log.info("Well.ca %s: read %d of %s listed products (the site's own paging stops early)",
                 cat, len(seen), total if total is not None else "?")
    if not products:
        raise RuntimeError("no products parsed from category pages -- site layout changed?")
    return list(products.values())


# --- WooCommerce stores (public Store API) -------------------------------------------------
# /wp-json/wc/store/v1/products?category=<slug>&per_page=100&page=N returns name, permalink,
# prices (in cents), is_in_stock and categories. Variable products (flavours) share one price
# at the parent level, so we track the product rather than fetching every variation.

def fetch_woocommerce(session: requests.Session, store: dict, cfg: dict,
                      keep: Callable[[str, str], bool]) -> list[dict]:
    p = cfg["polling"]
    base = store["base_url"].rstrip("/")
    home = session.get(base + "/", timeout=p["request_timeout_seconds"])
    if "undergoing maintenance" in home.text.lower():
        # Orders can't be placed, so don't alert; the store starts working again by itself.
        log.warning("%s storefront is in maintenance mode -- skipping this run", store["name"])
        return []
    time.sleep(p["delay_between_requests_seconds"])

    products: dict[int, dict] = {}
    for cat in store.get("categories") or [None]:
        for page in range(1, p["max_pages"] + 1):
            params = {"per_page": 100, "page": page}
            if cat:
                params["category"] = cat
            r = session.get(f"{base}/wp-json/wc/store/v1/products", params=params,
                            timeout=p["request_timeout_seconds"])
            r.raise_for_status()
            batch = r.json()
            for item in batch:
                pr = item.get("prices") or {}
                unit = 10 ** int(pr.get("currency_minor_unit", 2))
                rng = pr.get("price_range") or {}
                price = rng.get("min_amount") or pr.get("price")
                regular = pr.get("regular_price")
                if not price:
                    continue
                price_f, regular_f = int(price) / unit, (int(regular) / unit if regular else None)
                title = html.unescape(item.get("name", "")).strip()
                products[item["id"]] = {
                    "id": item["id"],
                    "title": title,
                    "handle": str(item["id"]),
                    "url": item.get("permalink") or base,
                    "product_type": " ".join(c.get("name", "") for c in item.get("categories", [])),
                    "body_html": item.get("short_description", ""),
                    "images": [{"src": item["images"][0]["src"]}] if item.get("images") else [],
                    "variants": [{
                        "id": item["id"],
                        "title": "Default Title",
                        "price": f"{price_f:.2f}",
                        "compare_at_price": f"{regular_f:.2f}" if regular_f and regular_f > price_f else None,
                        "available": bool(item.get("is_in_stock")) and item.get("is_purchasable", True),
                    }],
                }
            if len(batch) < 100:
                break
            time.sleep(p["delay_between_requests_seconds"])
        time.sleep(p["delay_between_requests_seconds"])
    return list(products.values())


READERS = {"proteinarabais": fetch_proteinarabais, "wellca": fetch_wellca,
           "woocommerce": fetch_woocommerce}
