#!/usr/bin/env python3
"""
Maison de Cocon -> idealo Business Center CSV feed builder.

Standalone from build_feed.py (the Google Merchant Center pipeline) by
deliberate design: GMC previously triggered a Misrepresentation suspension
that took real work to resolve, so this file must carry zero regression risk
to that pipeline. It imports and reuses build_feed's loaders/validators --
load_products_from_csv, load_products_from_shopify_api, validate_row,
is_known_sample_value, gtin_checksum_valid, ProductRow, MARKETS -- so idealo
and GMC can never silently diverge on which offers are eligible, but it never
modifies build_feed.py and never shares an output file, exclusions log, or
report with it.

Two input modes, same as build_feed.py:
  --source csv <path>      Reads a Shopify "Export products" CSV, for
                            offline runs and testing.
  --source shopify-api      Reads live product data via the Shopify Admin
                            GraphQL API (needs SHOPIFY_STORE_DOMAIN /
                            SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET).

--market <key>             (--source shopify-api only) Selects locale/link
                            prefix -- see build_feed.MARKETS. Default: de.

Own pipeline (own function in this file, not build_feed.run_pipeline):
mirrors run_pipeline's validate -> reject-samples -> write shape -- each row
is run through is_known_sample_value then validate_row exactly as
build_feed.run_pipeline does, and only accepted rows are written, to
idealo's own CSV format (see IDEALO_COLUMNS).

categoryPath: derived from the Shopify product's "Type" field when it's
present -- a single, unambiguous string per product. Collections were
considered and rejected as a source: a product can belong to several
collections with no defined path/order, so mapping one would mean guessing.
Fetching this needs its own, separate GraphQL query string (IDEALO_CATEGORY_
QUERY below) since build_feed.PRODUCTS_QUERY doesn't request productType and
must not be touched to add it. A product with no Type gets categoryPath=""
-- never guessed.

Output: data/idealo_feed.csv, plus its own exclusions log
(data/idealo_feed_exclusions.csv) and markdown report
(reports/YYYY-MM-DD_idealo.md).
"""
import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

import requests

import build_feed
from build_feed import (
    MARKETS,
    ProductRow,  # noqa: F401  -- re-exported for parity with build_feed's shape; not constructed directly here
    gtin_checksum_valid,  # noqa: F401  -- reused indirectly via validate_row/loaders; kept importable for tests
    is_known_sample_value,
    validate_row,
    load_products_from_csv,
    load_products_from_shopify_api,
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# idealo CSV Feed Import spec -- General formatting rules: comma-delimited,
# UTF-8, ";" for in-column lists (imageUrls here).
# ---------------------------------------------------------------------------
IDEALO_COLUMNS = [
    "sku", "brand", "title", "url", "eans", "description", "price",
    "categoryPath", "size", "colour", "deliveryCosts_dpd",
    "paymentCosts_paypal", "paymentCosts_credit_card", "delivery", "imageUrls",
]

# Matches this shop's GMC shipping policy: 1-2 day handling + 3-5 day
# transit, expressed to idealo as a single delivery-time string.
DELIVERY_TEXT = "4-7 working days"

# Flat shipping cost per offer, by price tier -- matches the GMC/Business
# Center shipping policy: (inclusive upper bound, cost). Anything above the
# last bound, or a price that doesn't parse, falls to SHIPPING_TOP_TIER --
# deliveryCosts_dpd must never be blank.
SHIPPING_TIERS = [
    (700.00, "10.00"),
    (1500.00, "120.00"),
]
SHIPPING_TOP_TIER = "300.00"

PAYMENT_COST = "0.00"


def shipping_cost_for_price(price_amount):
    """Flat deliveryCosts_dpd for one offer, per the tiered policy above.
    An unparseable/missing price falls back to the top tier rather than
    ever leaving this column blank."""
    try:
        price = float(price_amount)
    except (TypeError, ValueError):
        return SHIPPING_TOP_TIER
    for ceiling, cost in SHIPPING_TIERS:
        if price <= ceiling:
            return cost
    return SHIPPING_TOP_TIER


def merge_image_urls(row):
    """idealo's imageUrls column: image_link + additional_image_link,
    semicolon-joined per idealo's in-column-list convention. GMC's own
    additional_image_link column stays comma-joined -- untouched, this only
    reads and re-joins it for idealo's own column."""
    images = []
    image_link = (row.get("image_link") or "").strip()
    if image_link:
        images.append(image_link)
    additional = (row.get("additional_image_link") or "").strip()
    if additional:
        images.extend(u.strip() for u in additional.split(",") if u.strip())
    return ";".join(images)


# ---------------------------------------------------------------------------
# categoryPath source: Shopify's product "Type" field, keyed by handle.
# Deliberately its own, separate lookup -- NOT part of the ProductRow shape
# build_feed's loaders return, so it can never risk changing what those
# loaders hand back to build_feed.py's own callers.
# ---------------------------------------------------------------------------
IDEALO_CATEGORY_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    edges { node { handle productType } }
  }
}
"""


def load_category_paths_from_shopify_api(shop_domain, client_id, client_secret):
    """Own, idealo-only GraphQL query (separate from build_feed.PRODUCTS_
    QUERY) that pulls just handle + productType, paginated the same way as
    build_feed's loader. Returns {handle: productType}, skipping products
    with a blank type -- categoryPath is left blank for those rather than
    guessed. Reuses build_feed._get_access_token so a token already fetched
    by load_products_from_shopify_api() in the same run is not re-fetched."""
    token = build_feed._get_access_token(shop_domain, client_id, client_secret)
    url = f"https://{shop_domain}/admin/api/{build_feed.API_VERSION}/graphql.json"
    mapping = {}
    cursor = None
    while True:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": IDEALO_CATEGORY_QUERY, "variables": {"cursor": cursor}},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            raise RuntimeError(payload["errors"])
        block = payload["data"]["products"]
        for edge in block["edges"]:
            node = edge["node"]
            handle = node["handle"]
            product_type = (node.get("productType") or "").strip()
            if product_type:
                mapping[handle] = product_type
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return mapping


def load_category_paths_from_csv(path):
    """Shopify's product export CSV carries a product's Type in a "Type"
    column, populated only on that handle's first row and blank on every
    row after -- carried forward per handle the same way
    build_feed.load_products_from_csv carries Title/Vendor, without needing
    to touch that function."""
    mapping = {}
    carry = {}
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            handle = (raw.get("Handle") or "").strip()
            if not handle:
                continue
            product_type = (raw.get("Type") or "").strip()
            if product_type:
                carry[handle] = product_type
            if handle in carry:
                mapping[handle] = carry[handle]
    return mapping


# ---------------------------------------------------------------------------
# Pipeline: validate -> reject samples -> write idealo CSV + exclusions +
# report. Mirrors build_feed.run_pipeline's shape but is its own function --
# no shared output path, no shared report.
# ---------------------------------------------------------------------------
def run_idealo_pipeline(rows, category_paths, out_basename, run_label):
    accepted, excluded, sample_rejected = [], [], []

    for row in rows:
        sample_reason = is_known_sample_value(row)
        if sample_reason:
            sample_rejected.append((row, sample_reason))
            continue

        is_valid, reasons = validate_row(row)
        if not is_valid:
            excluded.append((row, reasons))
            continue

        accepted.append(row)

    feed_path = DATA_DIR / f"{out_basename}.csv"
    with open(feed_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=IDEALO_COLUMNS)
        writer.writeheader()
        for row in accepted:
            writer.writerow({
                "sku": row.get("id", ""),
                "brand": row.get("brand", ""),
                "title": row.get("title", ""),
                "url": row.get("link", ""),
                "eans": row.get("gtin", ""),
                "description": row.get("description", ""),
                "price": row.get("price_amount", ""),
                "categoryPath": category_paths.get(row.get("handle", ""), ""),
                "size": row.get("size", ""),
                "colour": row.get("color", ""),
                "deliveryCosts_dpd": shipping_cost_for_price(row.get("price_amount")),
                "paymentCosts_paypal": PAYMENT_COST,
                "paymentCosts_credit_card": PAYMENT_COST,
                "delivery": DELIVERY_TEXT,
                "imageUrls": merge_image_urls(row),
            })

    exclusions_path = DATA_DIR / f"{out_basename}_exclusions.csv"
    with open(exclusions_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "title", "reason", "category"])
        for row, reasons in excluded:
            writer.writerow([row.get("id", ""), row.get("title", ""), "; ".join(reasons), "validation"])
        for row, reason in sample_rejected:
            writer.writerow([row.get("id", ""), row.get("title", ""), reason, "sample_data"])

    report_lines = [
        f"# idealo feed build report -- {run_label}",
        "",
        f"- Rows read: {len(rows)}",
        f"- Accepted into feed: {len(accepted)}",
        f"- Excluded (validation failures): {len(excluded)}",
        f"- Rejected (known Google sample/placeholder data): {len(sample_rejected)}",
        "",
    ]
    if sample_rejected:
        report_lines.append(f"## Sample-data rejects ({len(sample_rejected)}) -- root-cause guard fired")
        report_lines.append(
            "These rows matched known Google documentation sample/placeholder values "
            "and were hard-rejected regardless of whether they otherwise looked valid:"
        )
        for row, reason in sample_rejected:
            report_lines.append(f"- `{row.get('id')}` {row.get('title')}: {reason}")
        report_lines.append("")
    if excluded:
        report_lines.append(f"## Validation exclusions ({len(excluded)})")
        for row, reasons in excluded:
            report_lines.append(f"- `{row.get('id') or '(no id)'}` {row.get('title') or '(no title)'}: {'; '.join(reasons)}")
        report_lines.append("")

    report_path = REPORTS_DIR / f"{date.today().isoformat()}_idealo.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return {
        "rows_read": len(rows),
        "accepted": len(accepted),
        "excluded": len(excluded),
        "sample_rejected": len(sample_rejected),
        "feed_path": feed_path,
        "exclusions_path": exclusions_path,
        "report_path": report_path,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["csv", "shopify-api"], required=True)
    parser.add_argument("--csv-path", help="Path to a Shopify product export CSV (required for --source csv)")
    parser.add_argument("--market", choices=sorted(MARKETS), default="de",
                         help="Target market/locale to build for (--source shopify-api only; see build_feed.MARKETS). Default: de")
    parser.add_argument("--out", default="idealo_feed", help="Output basename under data/ (default: idealo_feed)")
    args = parser.parse_args()

    if args.source == "csv":
        if not args.csv_path:
            parser.error("--csv-path is required when --source csv")
        if args.market != "de":
            parser.error("--market is not supported with --source csv (the export CSV carries no locale selection)")
        rows = load_products_from_csv(args.csv_path)
        category_paths = load_category_paths_from_csv(args.csv_path)
        run_label = f"csv:{args.csv_path} on {date.today().isoformat()}"
    else:
        shop_domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
        client_id = os.environ.get("SHOPIFY_CLIENT_ID")
        client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
        if not all([shop_domain, client_id, client_secret]):
            print("ERROR: SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET must all be set for --source shopify-api", file=sys.stderr)
            sys.exit(1)
        rows = load_products_from_shopify_api(shop_domain, client_id, client_secret, market=args.market)
        category_paths = load_category_paths_from_shopify_api(shop_domain, client_id, client_secret)
        run_label = f"shopify-api:{shop_domain} market={args.market} on {date.today().isoformat()}"

    stats = run_idealo_pipeline(rows, category_paths, args.out, run_label)
    print(
        f"rows_read={stats['rows_read']} accepted={stats['accepted']} "
        f"excluded={stats['excluded']} sample_rejected={stats['sample_rejected']}"
    )
    print(f"Feed written to {stats['feed_path']}")
    print(f"Exclusions logged to {stats['exclusions_path']}")
    print(f"Report written to {stats['report_path']}")


if __name__ == "__main__":
    main()
