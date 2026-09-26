#!/usr/bin/env python3
"""Short-dated protein bar deal tracker for Canadian Shopify stores.

Usage:
    python deal_tracker.py              # poll, alert via ntfy, update state
    python deal_tracker.py --dry-run    # poll and print; no alerts, no state writes
    python deal_tracker.py --force      # ignore the minimum poll interval
    python deal_tracker.py --snapshot-only --force   # refresh docs/deals.json only
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from parsing import is_bar_or_snack, parse_best_before, parse_count

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("deal_tracker")


@dataclass
class Deal:
    store: str
    product_id: int
    variant_id: int
    product_title: str
    variant_title: str
    url: str
    price: float
    compare_at: float | None
    discount_pct: float
    count: int
    count_known: bool
    per_bar: float
    best_before: date | None
    date_source: str | None
    days_left: int | None
    in_stock: bool = True
    image: str | None = None

    @property
    def key(self) -> str:
        return f"{self.store}:{self.product_id}:{self.variant_id}"

    @property
    def flavor(self) -> str:
        return "" if self.variant_title == "Default Title" else self.variant_title


# --- Config / state ----------------------------------------------------------

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def today_in(tz_name: str) -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # tzdata missing (e.g. bare Windows Python) -> local date
        return date.today()


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.error("State file %s is corrupt; starting fresh", path)
    return {"version": 1, "last_run": None, "items": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- Fetching ----------------------------------------------------------------

def make_session(cfg: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": cfg["polling"]["user_agent"],
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-CA,en;q=0.9",
    })
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch_collection(session: requests.Session, store: dict, cfg: dict) -> list[dict]:
    p = cfg["polling"]
    url = f"{store['base_url'].rstrip('/')}/collections/{store['collection']}/products.json"
    products: list[dict] = []
    for page in range(1, p["max_pages"] + 1):
        r = session.get(url, params={"limit": p["page_size"], "page": page},
                        timeout=p["request_timeout_seconds"])
        r.raise_for_status()
        batch = r.json().get("products", [])
        if not batch:
            break
        products.extend(batch)
        if len(batch) < p["page_size"]:
            break
        time.sleep(p["delay_between_requests_seconds"])
    return products


# --- Evaluation --------------------------------------------------------------

def _strip_html(s: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def _thumb(src: str | None) -> str | None:
    """Shopify CDN resizes on the fly with ?width=."""
    if not src:
        return None
    return f"{src}{'&' if '?' in src else '?'}width=160"


def to_deals(store: dict, products: list[dict], cfg: dict, today: date) -> tuple[list[Deal], dict]:
    """Turn raw products into bar/snack variant Deals (in_stock flags availability).

    Returns (deals, counters)."""
    f, th, order = cfg["filter"], cfg["thresholds"], cfg["dates"]["numeric_order"]
    stats = {"products": len(products), "not_bar": 0, "out_of_stock": 0}
    deals: list[Deal] = []
    for prod in products:
        title = html.unescape(prod.get("title", "")).strip()
        if not is_bar_or_snack(title, f["include"], f["exclude"]):
            stats["not_bar"] += 1
            continue
        body_date = parse_best_before(_strip_html(prod.get("body_html")), order, keyword_only=True)
        title_date = parse_best_before(title, order)
        images = {img.get("id"): img.get("src") for img in prod.get("images", [])}
        default_img = prod["images"][0]["src"] if prod.get("images") else None
        for v in prod.get("variants", []):
            in_stock = bool(v.get("available", False))
            if not in_stock:
                stats["out_of_stock"] += 1
            vt = html.unescape(v.get("title", "")).strip()
            try:
                price = float(v["price"])
            except (KeyError, TypeError, ValueError):
                continue
            cmp_raw = v.get("compare_at_price")
            compare_at = float(cmp_raw) if cmp_raw not in (None, "", "0.00") else None
            discount = round((compare_at - price) / compare_at * 100, 1) if compare_at and compare_at > price else 0.0

            count = parse_count(vt) or parse_count(title)
            count_known = count is not None
            count = count or int(th["assume_count_if_unknown"])

            # Variant-level date wins (flavors often carry different dates), then title, then description.
            bb, src = parse_best_before(vt, order), "variant"
            if bb is None:
                bb, src = title_date, "title"
            if bb is None:
                bb, src = body_date, "description"
            if bb is None:
                src = None

            deals.append(Deal(
                store=store["name"], product_id=prod["id"], variant_id=v["id"],
                product_title=title, variant_title=vt,
                url=f"{store['base_url'].rstrip('/')}/products/{prod['handle']}?variant={v['id']}",
                price=price, compare_at=compare_at, discount_pct=discount,
                count=count, count_known=count_known, per_bar=round(price / count, 2),
                best_before=bb, date_source=src,
                days_left=(bb - today).days if bb else None,
                in_stock=in_stock,
                image=_thumb((v.get("featured_image") or {}).get("src") or images.get(v.get("image_id")) or default_img),
            ))
    return deals, stats


def qualifies(d: Deal, th: dict) -> tuple[bool, str]:
    if d.per_bar > th["max_price_per_bar"]:
        return False, f"${d.per_bar:.2f}/bar > ${th['max_price_per_bar']:.2f}"
    if d.days_left is None:
        return (True, "no BB date") if th["allow_missing_best_before"] else (False, "no BB date")
    if d.days_left < th["min_days_before_best_before"]:
        return False, "expired" if d.days_left < 0 else f"only {d.days_left}d left"
    return True, "ok"


def alert_reason(d: Deal, state_items: dict, th: dict) -> str | None:
    prev = state_items.get(d.key)
    if prev is None:
        return "new"
    if d.price <= prev["price"] - th["min_price_drop"]:
        return f"price drop (was ${prev['price']:.2f})"
    return None


# --- Notifications -----------------------------------------------------------

def _deal_line(d: Deal, reason: str) -> str:
    flavor = f"{d.flavor} — " if d.flavor else ""
    disc = f" (-{d.discount_pct:.0f}%)" if d.discount_pct else ""
    bb = f"BB {d.best_before:%Y-%m-%d} ({d.days_left}d)" if d.best_before else "no BB date listed"
    cnt = f"{d.count}" if d.count_known else f"{d.count}?"
    return f"• {flavor}${d.price:.2f} for {cnt} = ${d.per_bar:.2f}/bar{disc} · {bb} · {reason}"


def build_messages(alerts: list[tuple[Deal, str]], max_msgs: int) -> list[dict]:
    """One notification per product listing; overflow bundled into a summary."""
    groups: dict[tuple[str, int], list[tuple[Deal, str]]] = {}
    for d, reason in alerts:
        groups.setdefault((d.store, d.product_id), []).append((d, reason))
    ordered = sorted(groups.values(), key=lambda g: min(x[0].per_bar for x in g))
    msgs = []
    for g in ordered[:max_msgs]:
        first = g[0][0]
        best = min(x[0].per_bar for x in g)
        msgs.append({
            "title": f"{first.store}: {first.product_title} — ${best:.2f}/bar",
            "message": "\n".join(_deal_line(d, r) for d, r in g),
            "click": first.url,
            "tags": ["chocolate_bar"],
        })
    rest = ordered[max_msgs:]
    if rest:
        lines = [f"• {g[0][0].store}: {g[0][0].product_title} — ${min(x[0].per_bar for x in g):.2f}/bar"
                 for g in rest]
        msgs.append({"title": f"+{len(rest)} more bar deals", "message": "\n".join(lines),
                     "tags": ["chocolate_bar"]})
    return msgs


def send_ntfy(session: requests.Session, cfg: dict, msg: dict) -> None:
    server = os.environ.get("NTFY_SERVER") or cfg["alerts"]["ntfy_server"]
    payload = {"topic": os.environ["NTFY_TOPIC"], "priority": cfg["alerts"]["priority"], **msg}
    headers = {}
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    # JSON publishing keeps emoji/accents intact (HTTP headers must be latin-1).
    r = session.post(server.rstrip("/"), json=payload, headers=headers, timeout=15)
    r.raise_for_status()


# --- Main --------------------------------------------------------------------

def print_table(deals: list[Deal], th: dict) -> None:
    rows = sorted(deals, key=lambda d: (not qualifies(d, th)[0], d.per_bar))
    print(f"\n{'OK':<3}{'$/bar':>7}{'price':>8}{'n':>4}{'off':>5}  {'best before':<16}{'store':<18}product / flavor")
    print("-" * 120)
    for d in rows:
        ok, why = qualifies(d, th)
        bb = f"{d.best_before:%Y-%m-%d} {d.days_left:>4}d" if d.best_before else "—"
        n = f"{d.count}" if d.count_known else f"{d.count}?"
        name = d.product_title + (f" / {d.flavor}" if d.flavor else "")
        flag = "✅" if ok else "  "
        note = "" if ok else f"   [{why}]"
        print(f"{flag:<3}{d.per_bar:>7.2f}{d.price:>8.2f}{n:>4}{d.discount_pct:>4.0f}%  {bb:<16}{d.store[:17]:<18}{name[:70]}{note}")


def run(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    th = cfg["thresholds"]
    state_path = ROOT / cfg["state"]["path"]
    state = load_state(state_path)
    now = datetime.now(timezone.utc)

    last = state.get("last_run")
    min_gap = timedelta(minutes=cfg["polling"]["min_interval_minutes"])
    if last and not args.force and now - datetime.fromisoformat(last) < min_gap:
        log.info("Last poll was %s; minimum interval is %s. Skipping (use --force).", last, min_gap)
        return 0

    today = today_in(cfg["dates"]["timezone"])
    session = make_session(cfg)
    items: dict = state.setdefault("items", {})
    all_deals: list[Deal] = []
    store_status: dict[str, dict] = {}
    failures = 0

    for i, store in enumerate(cfg["stores"]):
        if i:
            time.sleep(cfg["polling"]["delay_between_requests_seconds"])
        try:
            products = fetch_collection(session, store, cfg)
            if not products:
                log.warning("%-24s returned 0 products -- collection empty or handle '%s' renamed?",
                            store["name"], store["collection"])
            deals, stats = to_deals(store, products, cfg, today)
            all_deals.extend(deals)
            in_stock = sum(d.in_stock for d in deals)
            store_status[store["name"]] = {"ok": True, "products": stats["products"], "bar_variants": in_stock}
            log.info("%-24s %3d products, %3d not bars/snacks, %3d OOS variants, %3d in-stock bar variants",
                     store["name"], stats["products"], stats["not_bar"], stats["out_of_stock"], in_stock)
        except Exception as e:  # one broken store must not stop the others
            failures += 1
            store_status[store["name"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            log.error("%-24s FAILED: %s: %s", store["name"], type(e).__name__, e)

    stock = [d for d in all_deals if d.in_stock]
    alerts = []
    for d in stock:
        ok, _ = qualifies(d, th)
        reason = alert_reason(d, items, th) if ok else None
        if reason:
            alerts.append((d, reason))

    if args.verbose or args.dry_run:
        print_table(stock, th)
    log.info("%d in-stock bar/snack variants, %d qualify, %d to alert",
             len(stock), sum(qualifies(d, th)[0] for d in stock), len(alerts))

    snapshot_path = ROOT / cfg["site"]["snapshot_path"]
    if args.snapshot_only:
        write_snapshot(snapshot_path, all_deals, items, store_status, cfg, now)
        log.info("Wrote %s (no alerts sent, state untouched)", snapshot_path)
        return 0

    msgs = build_messages(alerts, cfg["alerts"]["max_notifications_per_run"])
    if args.dry_run:
        print(f"\n--- {len(msgs)} notification(s) that would be sent ---")
        for m in msgs:
            print(f"\n[{m['title']}]\n{m['message']}\n{m.get('click', '')}")
        return 1 if failures == len(cfg["stores"]) else 0

    # Only record an alerting item's new price once its notification actually went out,
    # so a failed send is retried next run instead of being silently lost.
    sent_ok = True
    if msgs:
        if not os.environ.get("NTFY_TOPIC"):
            log.error("NTFY_TOPIC is not set; %d notification(s) not sent", len(msgs))
            sent_ok = False
        else:
            for m in msgs:
                try:
                    send_ntfy(session, cfg, m)
                except Exception as e:
                    sent_ok = False
                    log.error("ntfy send failed for %r: %s", m["title"], e)
    alert_keys = {d.key for d, _ in alerts}

    stamp = now.isoformat(timespec="seconds")
    for d in stock:
        if d.key in alert_keys and not sent_ok:
            continue
        prev = items.get(d.key, {})
        entry = {
            "store": d.store, "title": d.product_title, "flavor": d.flavor, "url": d.url,
            "price": d.price, "per_bar": d.per_bar, "count": d.count,
            "best_before": d.best_before.isoformat() if d.best_before else None,
            "first_seen": prev.get("first_seen", stamp), "last_seen": stamp,
            "prev_price": prev.get("prev_price"), "price_changed_at": prev.get("price_changed_at"),
        }
        if prev and abs(prev["price"] - d.price) >= 0.005:
            entry["prev_price"], entry["price_changed_at"] = prev["price"], stamp
        items[d.key] = entry
    cutoff = now - timedelta(days=cfg["state"]["forget_after_days"])
    for k in [k for k, v in items.items() if datetime.fromisoformat(v["last_seen"]) < cutoff]:
        del items[k]
    state["last_run"] = stamp
    save_state(state_path, state)
    write_snapshot(snapshot_path, all_deals, items, store_status, cfg, now)

    if failures == len(cfg["stores"]):
        log.error("All stores failed")
        return 1
    return 0 if sent_ok else 2


def write_snapshot(path: Path, deals: list[Deal], items: dict, store_status: dict,
                   cfg: dict, now: datetime) -> None:
    """Write the JSON the web page reads. Stores that failed this run keep their
    previous rows (marked stale) so one outage doesn't blank the page."""
    th = cfg["thresholds"]
    stamp = now.isoformat(timespec="seconds")
    prev: dict = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    prev_stores = prev.get("stores", {})

    rows = []
    for d in deals:
        ok, why = qualifies(d, th)
        st = items.get(d.key, {})
        rows.append({
            "key": d.key, "store": d.store, "product": d.product_title, "flavor": d.flavor,
            "url": d.url, "image": d.image, "in_stock": d.in_stock,
            "price": d.price, "compare_at": d.compare_at, "discount_pct": d.discount_pct,
            "count": d.count, "count_known": d.count_known, "per_bar": d.per_bar,
            "best_before": d.best_before.isoformat() if d.best_before else None,
            "date_source": d.date_source, "days_left": d.days_left,
            "qualifies": ok and d.in_stock, "reason": why if d.in_stock else "out of stock",
            "first_seen": st.get("first_seen"),
            "prev_price": st.get("prev_price"), "price_changed_at": st.get("price_changed_at"),
            "stale": False,
        })

    stores = {}
    for s in cfg["stores"]:
        name = s["name"]
        status = store_status.get(name, {"ok": False, "error": "not polled"})
        url = f"{s['base_url'].rstrip('/')}/collections/{s['collection']}"
        if status["ok"]:
            stores[name] = {**status, "url": url, "last_ok": stamp}
        else:
            stores[name] = {**status, "url": url, "last_ok": prev_stores.get(name, {}).get("last_ok")}
            rows += [{**r, "stale": True} for r in prev.get("items", []) if r.get("store") == name]

    snapshot = {
        "generated_at": stamp,
        # Earliest first_seen: items first seen in the initial run aren't really "new".
        "tracking_since": min((v["first_seen"] for v in items.values() if v.get("first_seen")), default=None),
        "poll_interval_minutes": cfg["polling"]["min_interval_minutes"],
        "thresholds": {k: th[k] for k in ("max_price_per_bar", "min_days_before_best_before",
                                          "allow_missing_best_before")},
        "stores": stores,
        "items": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--dry-run", action="store_true", help="print results; send nothing, write nothing")
    ap.add_argument("--force", action="store_true", help="ignore min_interval_minutes")
    ap.add_argument("--snapshot-only", action="store_true",
                    help="fetch and write the web page's deals.json only; no alerts, state untouched")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the full deal table")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
