# Protein Bar Deal Tracker

Watches 21 Canadian and US supplement stores and pushes an
[ntfy.sh](https://ntfy.sh) notification when a short-dated protein bar or snack is a real deal.

**Canadian (CAD):**
- **Original four:** SupplementSource, Vita-Plus, Chicks and Muscles, Top Nutrition & Fitness.
- **Added from research:** Fitshop.ca, Protein Depot, Supplements Canada, Canadian Protein,
  Vitamart, 2GuysOnline, Nutrition House, Believe Supplements.
- **Custom reader:** Protein à Rabais, which isn't on Shopify, so
  [`custom_stores.py`](custom_stores.py) reads its liquidation, protein bar, chocolate and
  grocery & snacks categories. It opens product pages only for snack-looking items, about
  30 pages. Its French titles, pack sizes ("Boîte de 12", "12 barres") and dates
  ("exp 05/2027", "AOUT 2025", "LIQUIDATION 7x 06/26") are all parsed.
- **Custom reader:** Well.ca. It reads its fitness & protein clearance and protein bars &
  snacks categories. The site's own paging breaks after page 2 (for real visitors too), so the
  reader merges page 1 of every sort order. That reaches about 77 of 82 clearance items and
  about 260 of 680 bars. Well.ca doesn't show best-before dates, so its items fall under the
  "undated" rule.
- **Custom reader:** Supplements Direct (Vancouver, WooCommerce), read through its public
  Store API. Its storefront currently says "Site is undergoing maintenance", so the reader
  skips it until that message is gone.

2GuysOnline is a general grocery liquidator, so only items with "protein" in the title are
tracked there (`require_words`).

**US (USD, converted to CAD):** Best Price Nutrition, Supplement Warehouse, NutriCartel,
PoorBoy Supplements, Nutrition Depot (Houston), and Dive Bar (its Canada collection only). Each lists Canada in its
Shopify `ships_to_countries`. Prices are converted at the Bank of Canada's daily rate.
Cross-border shipping, duty and brokerage are **not** included, and alerts say so.

It scans whole catalogs instead of just "clearance" collections, because short-dated stock
often isn't filed there. On 2026-09-26 the clearance-only scan missed Built Puff at
$1.67/bar (Chicks and Muscles) and Misfits at $1.67/bar (Vita-Plus). A full poll takes
about 9 minutes. Vitamart's ~10,000 products are most of that.

### Stores considered but not added

| Store | Why |
|---|---|
| Supplement Hunt (US) | Doesn't ship outside the US (its Shopify ships-to list is US-only) |
| Fitdeals.ca | Domain doesn't resolve, so the site appears to be offline |
| Healthy Planet | Blocks automated access with a bot challenge |
| Muscle & Strength | Cloudflare bot challenge (403). Won't bypass |
| Popeye's | Clearance category is empty, prices load via JavaScript, behind Cloudflare |
| Myprotein CA | Its clearance page now redirects to general nutrition. No best-before labelling |
| Supplement King | robots.txt disallows every URL with `?`, so categories can't be paged. No clearance section |
| SameDaySupplements | US, full price, no clearance or short-dated section |
| Herc's | hercsnutrition.com is a parked domain. The real site wasn't found |
| Supplement Superstore | Now redirects to SupplementSource (already tracked) |
| Nutrition Depot (Canadian, SND Canada) | Now redirects to Supplements Canada (already tracked) |
| Lean Machine, Muscle Ave, Mr. Supplement (CA), allsupplements.ca | No such store found. The domains don't exist or are parked |
| Sportsfuel | Only a New Zealand store, which ships to NZ only |
| DPS Nutrition (US) | Doesn't ship internationally |
| Amazon.ca, eBay.ca, Flashfood, Too Good To Go, Winners, Costco, Kijiji, Facebook Marketplace | No public product feed, app-only or in-store, or scraping isn't permitted |

## How it works

For every store, the tracker pages through `products.json?limit=250&page=N` until it gets an
empty page. Then, for each product:

1. **Snack filter** (deliberately inclusive). A product is tracked when its title has no
   `exclude` word (drinks, sauces, powders, vitamins…) and either:
   - its title has an `include` word (bar, cookie, pastry, chips, cups, gummies, jerky…), or
   - the store itself filed it under a snack product type (`snack_types`, e.g. "Protein Snacks").

   Supplements and bulk goods are always skipped: "60 (Vegan) Gummies", "110 ct",
   20+ servings, or a single package of 250 g or more. A wrongly included item
   still has to beat the price threshold to alert, whereas a wrongly excluded one is a
   silently missed deal.
2. **Per variant** (each flavour/size is its own listing, often with its own date):
   - Out-of-stock variants are remembered, so a restock can alert.
   - **Bar count** comes from the variant title, then the product title: `12 Bars/Box`,
     `60g x 12`, `Box of 12`, `12/box`, `(1 bar)`, `SINGLE BAR`, `6 Pastry`, `21 cookies`,
     `12-pack`, `12 packs of 2 cookies` (12). Servings aren't units: `1 bag of 4 servings` counts as 1.
     If no count is stated, it assumes 1, so the price per bar is never understated.
   - **Best-before date** comes from the variant title, then the product title, then the
     description (description only when a keyword like "Best Before" introduces the date).
   - **Discount %** = `(compare_at_price − price) / compare_at_price`.
   - **Price per bar** = `price / count`.
3. **Alert** when the variant is **new**, its **price dropped**, or it's **back in stock**, *and*:
   - it costs ≤ `max_price_per_bar` per bar, *and*
   - it has at least `min_days_before_best_before` days left. If the listing has no date, it
     must instead be at least `undated_min_discount_pct` off, so cheap full-price single-serve
     snacks don't alert.
   Variants from the same product are grouped into one notification. The notification links
   to the exact variant.

### Supported date formats

Month-only dates resolve to the **last day of that month**.

| Example | Parsed as |
|---|---|
| `30-Nov-26`, `30 November 2026`, `07.Nov.26` | 2026-11-30 / 2026-11-07 |
| `Best Before 09/2026`, `BB 05/2026`, `Use by 11.2026` | end of that month |
| `Best Before 09/25`, `EXP 11-26` | end of that month (2-digit year only after a keyword) |
| `Best Before End of 11/2026` | 2026-11-30 |
| `BB April 2, 2026`, `Dec 15 2026`, `Expiry Date: November 30th, 2026` | exact day |
| `BB 03/31/26`, `B.B. 31/12/26`, `BB 15.11.2026` | exact day. If both numbers are ≤ 12, `numeric_order` decides |
| `Exp. Nov 2026`, `Best by Sept '26`, `Good until Jan-27` | end of that month |
| `2026-11-15`, `BBD: 2026/11` | exact day / end of month |

Recognized keywords include Best Before, Best Beore (a real typo on one store), Best By,
BB, BBD, B.B., Exp, Expiry, Expires, Expiration Date, Use By, Good Until, and Dated.
Unparseable dates such as `05/206` fall back to the product title's date.

## Configuration

All thresholds are in [`config.toml`](config.toml):

| Key | Default | Meaning |
|---|---|---|
| `thresholds.max_price_per_bar` | `2.50` | CAD per bar, inclusive |
| `thresholds.min_days_before_best_before` | `7` | days that must remain |
| `thresholds.min_price_drop` | `0.01` | smallest drop that counts as a price drop |
| `thresholds.allow_missing_best_before` | `true` | alert on cheap bars that list no date... |
| `thresholds.undated_min_discount_pct` | `20` | ...if they're at least this much off |
| `health.consecutive_failures` | `3` | failed polls in a row before a warning notification |
| `health.min_product_ratio` | `0.5` | warn if a store suddenly lists under half its usual products |
| `polling.min_interval_minutes` | `60` | refuses to poll more often (`--force` overrides) |
| `alerts.max_notifications_per_run` | `8` | extra deals get bundled into one summary |
| `dates.numeric_order` | `MDY` | how to read ambiguous `03/04/26` |

To add a store, append another `[[stores]]` block with `name` and `base_url`. That scans the
whole catalog. Optional keys:
- `collections = ["handle", ...]`: scan only certain collections.
- `currency = "USD"`: convert prices to CAD.
- `require_words = [...]`: only track titles containing one of these words.
- `platform = "..."`: use a custom reader from `custom_stores.py`.

**Tuning the filter:** open the dashboard's *Everything* view. A snack that's missing
needs a word in `include`, or its product type in `snack_types`. A non-snack that's
showing up needs a word in `exclude`. Then add the title to the regression tests in
`tests/test_parsing.py`.

The ntfy topic is **not** in the config. It is read from the `NTFY_TOPIC` environment variable.
Anyone who knows the topic name can read it, so pick something unguessable. Optional variables:
`NTFY_SERVER` (for a self-hosted server) and `NTFY_TOKEN` (for protected topics).

## Running locally

Requires Python 3.11+.

```bash
pip install -r requirements.txt
python deal_tracker.py --dry-run          # fetch + print full table and would-be alerts; writes nothing
NTFY_TOPIC=your-secret-topic python deal_tracker.py      # real run: alerts + updates state
python deal_tracker.py --force -v         # ignore the 60-min guard, print the table too
```

Tests: `pip install -r requirements-dev.txt && python -m pytest`

## State and repeat alerts

`state/seen.json` records every in-stock bar variant it has seen, with its last price,
per-bar price, best-before date, and first/last-seen timestamps. It also records the time
of the last poll. The file is committed back to the repo after each run, so you only get
alerted when something is new or cheaper.

- If a notification fails to send, that item's price is **not** recorded, so the alert is retried next run.
- Items not seen for `forget_after_days` (30) are dropped and count as new if they come back.
- Delete `state/seen.json` to start over.

## Dashboard (GitHub Pages)

`docs/index.html` is a static page that shows every tracked bar and snack, grouped by product
with one row per flavour. It reads `docs/deals.json`, which the tracker rewrites on every run
and the workflow deploys to Pages right after each poll.

`deals.json` isn't committed, because it's about 1.3 MB and changes hourly. Each run starts
from the live copy on the site instead.

- **Views:**
  - **Deals**: items that pass your thresholds.
  - **All in stock**: every in-stock item.
  - **Everything**: also shows out-of-stock flavours, struck through.
- Search, store filter, and sorting by price per bar, soonest best-before, biggest discount, or newest.
- **Badges:** NEW (first seen within 48 hours), PRICE DROP with the old price (within 7 days),
  and a best-before chip: red means under the minimum, amber means under 30 days, grey means
  the listing has no date.
- **Store chips** show each store's status. If a store fails on a run, its previous rows stay
  on the page marked STALE rather than disappearing.
- **Refreshing:** an open tab re-checks for new data every 5 minutes and whenever you switch
  back to it. Light and dark mode follow your system, and the ◐ button overrides it.

Preview locally:

```bash
python deal_tracker.py --snapshot-only --force
python -m http.server 8765 --directory docs
```

Then open http://localhost:8765. `--snapshot-only` only rewrites `deals.json`. It doesn't send
alerts or touch the state file.

## Safeguards against missed deals

| Risk | Safeguard |
|---|---|
| Deal filed outside "clearance" | Whole catalogs are scanned |
| Snack title lacks a known keyword | The store's own product type also counts |
| Count unparsed, so per-bar price overstated | Many count formats are handled. Unknown counts show as `12?` on the dashboard |
| Item sells out then restocks | "Back in stock" alert |
| A store breaks, blocks us, or changes its site | ntfy warning after 3 failed polls, or if the product count collapses. It says when the store recovers |
| Pagination cap reached | Logged as a warning |
| ntfy send fails | The item isn't marked as seen, so the alert retries next run |
| The whole workflow fails | GitHub emails you (on by default for failed scheduled runs) |
| A keyword edit breaks a known snack | Regression tests use real catalog titles and the real `config.toml` |

## Error handling

- Each store is fetched in its own `try` block, so one broken store is logged and the rest continue.
- Requests retry with backoff on 429 and 5xx, respecting `Retry-After`.
- There's a pause between requests, and the User-Agent is a normal browser one.
- Exit code 1 if every store failed. Exit code 2 if a notification failed. The workflow run shows red in either case.

## GitHub Actions setup

1. Push this folder to a **public** GitHub repo. GitHub Pages is only free for public repos.
   Nothing secret is committed: the ntfy topic lives in a repo secret.
2. **Settings → Secrets and variables → Actions → New repository secret**: `NTFY_TOPIC` = your topic.
3. **Settings → Actions → General → Workflow permissions** → *Read and write permissions*,
   so the workflow can commit `state/seen.json`.
4. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
5. Subscribe to the topic in the ntfy app (iOS/Android) or at `https://ntfy.sh/<topic>`.
6. Trigger it once from **Actions → Deal tracker → Run workflow**. The dashboard will be at
   `https://<your-username>.github.io/<repo-name>/`.

The workflow in [`.github/workflows/deal-tracker.yml`](.github/workflows/deal-tracker.yml) runs
hourly at :17. GitHub's scheduler can start runs late, and the tracker also enforces the
60-minute minimum itself. So an occasional scheduled run will skip, which means a two-hour gap
now and then. If you'd rather never skip, lower `min_interval_minutes` to about 50.
The hourly state commits also keep the repo active, so GitHub won't auto-disable the schedule
after 60 days without activity.
