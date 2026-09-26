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
Fetching this needs its own, separate GraphQL query string (IDEALO_LOOKUP_
QUERY below) since build_feed.PRODUCTS_QUERY doesn't request productType and
must not be touched to add it. The Shopify Type is English (e.g. "Duvet
Covers"); idealo requires German category paths, so it is translated via the
explicit CATEGORY_PATHS_DE table. A product with no Type, or a Type not in
that table, gets categoryPath="" -- never guessed, never sent in English --
and is listed in the report so the table can be extended.

url: idealo needs a direct link to the exact variant (so price/availability
on landing match the offer). build_feed's links carry "?variant_sku=<sku>",
which the storefront theme never reads, so Shopify opens the default variant.
This file rewrites each link to Shopify's native "?variant=<numeric variant
id>", using the variant ids from the same idealo-only IDEALO_LOOKUP_QUERY.
A row whose variant id can't be found (always the case for --source csv:
Shopify's product export carries no variant ids) keeps build_feed's link and
is counted in the report.

delivery: German text (idealo requires German delivery-time information).

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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
# transit, expressed to idealo as a single delivery-time string. Must be
# German -- idealo shows it verbatim on idealo.de and flagged the English
# "4-7 working days" in Technical Account Management review.
DELIVERY_TEXT = "4-7 Werktage"

# Vendors whose delivery time differs from DELIVERY_TEXT, keyed by the exact
# Shopify vendor name (same convention as VENDOR_SHIPPING_BY_COUNTRY).
# SalesFever ships from the supplier: 6-11 working days handling + 3-5
# transit = 9-16 working days, matching the PDP and Shopify's delivery window.
VENDOR_DELIVERY_TEXT = {
    "SalesFever": "9-16 Werktage",
}

# Shopify product Type (English, as set in Shopify) -> German idealo
# categoryPath, levels separated by " > ". Explicit table, no machine
# translation: a Type missing here yields a blank categoryPath and shows up
# in the report's "Unmapped product types" section, so a new Type (e.g. from
# a new supplier) is added here deliberately rather than guessed.
CATEGORY_PATHS_DE = {
    # Bettwäsche
    "Bedding": "Heimtextilien > Bettwäsche",
    "Bedding Sets": "Heimtextilien > Bettwäsche > Bettwäsche-Sets",
    "Duvet Covers": "Heimtextilien > Bettwäsche > Bettbezüge",
    "Pillowcases": "Heimtextilien > Bettwäsche > Kissenbezüge",
    "Fitted Sheets": "Heimtextilien > Bettwäsche > Spannbettlaken",
    "Flat Sheets": "Heimtextilien > Bettwäsche > Betttücher",
    # Decken & Überwürfe
    "Duvets": "Heimtextilien > Bettdecken",
    "Throws": "Heimtextilien > Wohndecken",
    "Bedspreads": "Heimtextilien > Tagesdecken",
    "Bed Runners": "Heimtextilien > Bettläufer",
    # Kissen, Topper, Matratzen, Betten
    "Pillows": "Heimtextilien > Kopfkissen",
    "Mattress Toppers": "Schlafzimmer > Matratzen > Topper",
    "Mattress Protectors": "Schlafzimmer > Matratzen > Matratzenschoner",
    "Mattresses": "Schlafzimmer > Matratzen",
    "Beds": "Schlafzimmer > Betten",
    "Luxury Beds": "Schlafzimmer > Betten",
    "Boxspring Beds": "Schlafzimmer > Betten > Boxspringbetten",
    "Upholstered Beds": "Schlafzimmer > Betten > Polsterbetten",
    "Headboards": "Schlafzimmer > Betten > Kopfteile",
    "Bed Benches": "Schlafzimmer > Bettbänke",
}

# Flat shipping cost per offer, by price tier -- matches the GMC/Business
# Center shipping policy: (inclusive upper bound, cost). Anything above the
# last bound, or a price that doesn't parse, falls to SHIPPING_TOP_TIER --
# deliveryCosts_dpd must never be blank.
SHIPPING_TIERS = [
    (700.00, "10.00"),
    (1500.00, "120.00"),
]
SHIPPING_TOP_TIER = "300.00"

# Country this feed is shown in. The feed is uploaded to idealo.de, whose
# offers show the shipping cost to Germany; idealo runs a separate portal
# (and feed) per country, so an idealo.at / idealo.fr feed would set this to
# "AT" / "FR".
FEED_COUNTRY = "DE"

# Vendors whose shipping is priced per destination country instead of by
# the price tiers above -- same rates as the Shopify/GMC shipping setup.
# Keyed by the exact Shopify vendor name. Overrides SHIPPING_TIERS for that
# vendor's offers in every feed country.
VENDOR_SHIPPING_BY_COUNTRY = {
    "SalesFever": {
        "DE": "119.00",
        "AT": "239.00",
        "BE": "239.00",
        "FR": "239.00",
        "LU": "239.00",
        "NL": "239.00",
    },
}

# SalesFever Shopify Types priced on the Small delivery profile instead of
# VENDOR_SHIPPING_BY_COUNTRY's Bulky rate above -- mirrors build_feed.py's
# SALESFEVER_SMALL_TYPES / GMC's sf_small label. Rate confirmed 2026-09-26
# against the Orderchamp "Supported Countries" table for the Storage Bed
# Bench (no per-unit surcharge).
SALESFEVER_SMALL_TYPES = {"Bed Benches"}
SALESFEVER_SMALL_SHIPPING_BY_COUNTRY = {
    "DE": "19.90",
    "AT": "79.00",
    "BE": "79.00",
    "FR": "79.00",
    "LU": "79.00",
    "NL": "79.00",
}

PAYMENT_COST = "0.00"


def shipping_cost_for_row(row, country=None, product_type=""):
    """deliveryCosts_dpd for one offer. A vendor listed in
    VENDOR_SHIPPING_BY_COUNTRY gets its rate for the feed's country (a
    SalesFever offer whose product_type is in SALESFEVER_SMALL_TYPES gets
    SALESFEVER_SMALL_SHIPPING_BY_COUNTRY instead); everyone else gets the
    price-tier cost. A listed vendor with no rate for the country is a
    config error and fails the build loudly rather than silently falling
    back to the (much lower) price tiers."""
    country = country or FEED_COUNTRY
    vendor = (row.get("brand") or "").strip()
    if vendor == "SalesFever" and (product_type or "").strip() in SALESFEVER_SMALL_TYPES:
        rates = SALESFEVER_SMALL_SHIPPING_BY_COUNTRY
        if country not in rates:
            raise ValueError(f"No {country} shipping rate configured for vendor '{vendor}' Bed Benches in SALESFEVER_SMALL_SHIPPING_BY_COUNTRY")
        return rates[country]
    if vendor in VENDOR_SHIPPING_BY_COUNTRY:
        rates = VENDOR_SHIPPING_BY_COUNTRY[vendor]
        if country not in rates:
            raise ValueError(f"No {country} shipping rate configured for vendor '{vendor}' in VENDOR_SHIPPING_BY_COUNTRY")
        return rates[country]
    return shipping_cost_for_price(row.get("price_amount"))


def delivery_text_for_row(row):
    """`delivery` for one offer: the vendor's own text if it is listed in
    VENDOR_DELIVERY_TEXT (exact vendor name), else DELIVERY_TEXT."""
    vendor = (row.get("brand") or "").strip()
    return VENDOR_DELIVERY_TEXT.get(vendor, DELIVERY_TEXT)


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
IDEALO_LOOKUP_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        handle
        productType
        variants(first: 100) { edges { node { sku legacyResourceId } } }
      }
    }
  }
}
"""


def load_idealo_lookups_from_shopify_api(shop_domain, client_id, client_secret):
    """Own, idealo-only GraphQL query (separate from build_feed.PRODUCTS_
    QUERY) that pulls handle + productType + each variant's sku and numeric
    id, paginated the same way as build_feed's loader (same page sizes as
    PRODUCTS_QUERY, which is proven against this store).

    Returns (product_types, variant_ids):
      product_types -- {handle: productType}, skipping blank types
      variant_ids   -- {(handle, sku): numeric variant id as str}

    Reuses build_feed._get_access_token so a token already fetched by
    load_products_from_shopify_api() in the same run is not re-fetched."""
    token = build_feed._get_access_token(shop_domain, client_id, client_secret)
    url = f"https://{shop_domain}/admin/api/{build_feed.API_VERSION}/graphql.json"
    product_types = {}
    variant_ids = {}
    cursor = None
    while True:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": IDEALO_LOOKUP_QUERY, "variables": {"cursor": cursor}},
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
                product_types[handle] = product_type
            for vedge in ((node.get("variants") or {}).get("edges") or []):
                v = vedge.get("node") or {}
                sku = (v.get("sku") or "").strip()
                vid = str(v.get("legacyResourceId") or "").strip()
                if sku and vid:
                    variant_ids[(handle, sku)] = vid
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return product_types, variant_ids


def german_category_path(product_type):
    """English Shopify Type -> German idealo categoryPath, or "" if the Type
    is blank or not in CATEGORY_PATHS_DE (never passed through in English)."""
    return CATEGORY_PATHS_DE.get((product_type or "").strip(), "")


def variant_url(link, variant_id):
    """Rewrites build_feed's product link to Shopify's native variant deep
    link: drops the theme-ignored "variant_sku" param and sets
    "variant=<numeric id>", keeping path (incl. locale prefix/localized
    handle) and any other params. Without a variant id the link is returned
    unchanged."""
    if not link or not variant_id:
        return link
    parts = urlsplit(link)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k not in ("variant_sku", "variant")]
    params.append(("variant", str(variant_id)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


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
def run_idealo_pipeline(rows, product_types, out_basename, run_label, variant_ids=None):
    """product_types: {handle: English Shopify Type}; translated to German
    via CATEGORY_PATHS_DE at write time. variant_ids: {(handle, sku):
    numeric variant id}; None/{} means no variant deep links are available
    (e.g. --source csv) and build_feed's links are kept as-is."""
    variant_ids = variant_ids or {}
    accepted, excluded, sample_rejected = [], [], []
    unmapped_types = {}      # English Type -> number of accepted offers affected
    no_type_handles = set()  # handles with no Shopify Type at all
    missing_variant_ids = []  # accepted rows whose url couldn't be deep-linked

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
            handle = row.get("handle", "")
            product_type = product_types.get(handle, "")
            category_path = german_category_path(product_type)
            if not product_type:
                no_type_handles.add(handle)
            elif not category_path:
                unmapped_types[product_type] = unmapped_types.get(product_type, 0) + 1

            vid = variant_ids.get((handle, row.get("id", "")))
            if not vid:
                missing_variant_ids.append(row)

            writer.writerow({
                "sku": row.get("id", ""),
                "brand": row.get("brand", ""),
                "title": row.get("title", ""),
                "url": variant_url(row.get("link", ""), vid),
                "eans": row.get("gtin", ""),
                "description": row.get("description", ""),
                "price": row.get("price_amount", ""),
                "categoryPath": category_path,
                "size": row.get("size", ""),
                "colour": row.get("color", ""),
                "deliveryCosts_dpd": shipping_cost_for_row(row, product_type=product_type),
                "paymentCosts_paypal": PAYMENT_COST,
                "paymentCosts_credit_card": PAYMENT_COST,
                "delivery": delivery_text_for_row(row),
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
        f"- Accepted offers without a variant deep link (url falls back to product page): {len(missing_variant_ids)}",
        f"- Accepted offers with blank categoryPath (no Type / unmapped Type): "
        f"{sum(1 for r in accepted if not german_category_path(product_types.get(r.get('handle', ''), '')))}",
        "",
    ]
    if unmapped_types:
        report_lines.append(f"## Unmapped product types ({len(unmapped_types)}) -- add to CATEGORY_PATHS_DE")
        report_lines.append("These Shopify Types have no German categoryPath yet, so their offers were sent with a blank categoryPath:")
        for ptype, count in sorted(unmapped_types.items()):
            report_lines.append(f"- `{ptype}`: {count} offer(s)")
        report_lines.append("")
    if no_type_handles:
        report_lines.append(f"## Products with no Shopify Type ({len(no_type_handles)})")
        for handle in sorted(no_type_handles):
            report_lines.append(f"- `{handle}`")
        report_lines.append("")
    if missing_variant_ids and variant_ids:
        # Only itemised when variant ids were loaded at all -- in csv mode
        # every row is missing one by design and the count above says so.
        report_lines.append(f"## Offers without a variant id ({len(missing_variant_ids)})")
        for row in missing_variant_ids:
            report_lines.append(f"- `{row.get('id')}` ({row.get('handle')})")
        report_lines.append("")
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
        "missing_variant_ids": len(missing_variant_ids),
        "unmapped_types": dict(unmapped_types),
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
        product_types = load_category_paths_from_csv(args.csv_path)
        variant_ids = {}  # Shopify's product export CSV carries no variant ids
        run_label = f"csv:{args.csv_path} on {date.today().isoformat()}"
    else:
        shop_domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
        client_id = os.environ.get("SHOPIFY_CLIENT_ID")
        client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
        if not all([shop_domain, client_id, client_secret]):
            print("ERROR: SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET must all be set for --source shopify-api", file=sys.stderr)
            sys.exit(1)
        rows = load_products_from_shopify_api(shop_domain, client_id, client_secret, market=args.market)
        product_types, variant_ids = load_idealo_lookups_from_shopify_api(shop_domain, client_id, client_secret)
        run_label = f"shopify-api:{shop_domain} market={args.market} on {date.today().isoformat()}"

    stats = run_idealo_pipeline(rows, product_types, args.out, run_label, variant_ids=variant_ids)
    print(
        f"rows_read={stats['rows_read']} accepted={stats['accepted']} "
        f"excluded={stats['excluded']} sample_rejected={stats['sample_rejected']} "
        f"missing_variant_ids={stats['missing_variant_ids']} unmapped_types={stats['unmapped_types']}"
    )
    print(f"Feed written to {stats['feed_path']}")
    print(f"Exclusions logged to {stats['exclusions_path']}")
    print(f"Report written to {stats['report_path']}")


if __name__ == "__main__":
    main()
