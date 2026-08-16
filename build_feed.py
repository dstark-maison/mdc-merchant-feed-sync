#!/usr/bin/env python3
"""
Maison de Cocon -> Google Merchant Center product feed builder.

Two input modes, one shared transform/validate/write pipeline:
  --source csv <path>      Reads a Shopify "Export products" CSV (the same file
                            format Products > Export produces in Shopify Admin).
                            Used for offline runs and for Layer 1's first upload.
  --source shopify-api      Reads live product data from the Shopify Admin
                            GraphQL API using OAuth client-credentials, the same
                            auth pattern cerda-sync/sync_angel_cerda.py uses
                            (Dev Dashboard apps have no static Admin API token).
                            Needs SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID /
                            SHOPIFY_CLIENT_SECRET set as env vars.

Both modes produce the same normalized ProductRow list, which then goes
through the SAME validate -> reject-known-samples -> write pipeline, so the
two input paths can never silently diverge in behavior.

Output: a Google Shopping product feed (tab-delimited .txt, the traditional
scheduled-fetch format) plus a comma-delimited .csv of the same data, an
exclusions log (rows that did NOT make it into the feed, with reasons), and a
markdown report summarizing the run -- same reports/YYYY-MM-DD.md pattern as
cerda-sync, so both pipelines read the same way.

Validation gate: a row missing a required field is EXCLUDED and logged with a
reason. It is never silently dropped and never silently published with a
blank/placeholder value in a required column -- see validate_row().

Sample-data hard check: rows matching known values from Google's own sample
Content API / Merchant Center documentation feeds are rejected outright and
logged separately from ordinary validation failures (see SAMPLE_*). This is
the root-cause guard for the original suspension: at some point a manually-
edited feed carried over Google's own documentation example row(s) instead of
real product data, and Google flagged the account for it.
"""
import argparse
import csv
import hashlib
import html.parser
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

STORE_DOMAIN_PUBLIC = "www.maisondecocon.com"  # storefront domain used in feed `link` values
API_VERSION = "2025-01"

# EU 2019/771 gives every EU consumer a minimum 2-year statutory conformity
# guarantee regardless of what a merchant's own return policy says. This is
# informational metadata on rows only -- see Phase 1 finding: hasMerchantReturnPolicy
# and shippingRate are already satisfied at the ACCOUNT level (Verified "Standard
# for Germany" return policy + a Complete DE shipping service covering all
# products), so this pipeline deliberately does NOT emit per-row
# shipping/return-policy feed columns. Expanding account-level return/shipping
# coverage to AT/FR/BE/LU is a manual Merchant Center follow-up, not something
# this pipeline maps per-row.
STATUTORY_GUARANTEE_YEARS = 2

# ---------------------------------------------------------------------------
# Known Google sample/placeholder values -- hard rejects, logged separately.
# Sourced from Google's own Merchant Center / Content API sample feed
# documentation (support.google.com/merchants/answer/7052112 and the Content
# API quickstart samples). If a row matches ANY of these, it is treated as
# leftover template/placeholder data accidentally left in a manually-edited
# feed -- never published, regardless of what else is valid about the row.
# ---------------------------------------------------------------------------
SAMPLE_IDS = {"1111111111", "111111111", "123456", "abc123", "sample_id"}
SAMPLE_TITLES = {
    "mens pique polo shirt",
    "sample product",
    "test product",
    "example product title",
    "example product",
}
SAMPLE_GTINS = {
    "000000000000",
    "0000000000000",
    "00000000000000",
    "3234567890126",  # Google's literal documented example GTIN
}
SAMPLE_BRANDS = {"google"}
SAMPLE_LINK_SUBSTRINGS = ("example.com",)

REQUIRED_FIELDS = ("id", "title", "description", "link", "image_link", "price", "availability")


class ProductRow(dict):
    """A single feed-eligible offer: one Shopify variant flattened to the
    field names Google's product feed spec uses. Plain dict subclass -- no
    behavior, just a documented shape so callers don't have to guess keys:
    id, title, description, link, image_link, price, availability, brand,
    condition, gtin, mpn, item_group_id, handle (handle is feed-internal,
    stripped before writing -- kept only for grouping/debugging)."""


def strip_html(raw):
    """Strips tags from Shopify's body_html and decodes entities, collapsing
    whitespace. Uses only the stdlib html.parser -- no external HTML library
    needed for this one-directional strip."""
    if not raw:
        return ""

    class _Stripper(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    stripper = _Stripper()
    stripper.feed(raw)
    stripper.close()
    text = "".join(stripper.parts)
    return re.sub(r"\s+", " ", text).strip()


def gtin_checksum_valid(gtin):
    """Standard GS1 mod-10 check digit validation for GTIN-8/12/13/14. A
    barcode that fails this check is either mistyped or placeholder data
    (e.g. all-zeros passes length checks but fails/trivially-passes checksum
    depending on length) -- used both by validation and the sample-data
    check below."""
    digits = re.sub(r"\D", "", gtin or "")
    if len(digits) not in (8, 12, 13, 14):
        return False
    nums = [int(d) for d in digits]
    check = nums[-1]
    body = nums[:-1][::-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - (total % 10)) % 10 == check


def is_known_sample_value(row):
    """Hard reject: does this row match a known Google documentation sample
    value? Returns a reason string if so, else None. Checked independently
    of validate_row() so a sample row that happens to have every "required"
    field present (as Google's own sample rows do -- they're deliberately
    complete/valid-looking) still gets caught."""
    pid = str(row.get("id", "")).strip().lower()
    title = str(row.get("title", "")).strip().lower()
    gtin = re.sub(r"\D", "", str(row.get("gtin", "")))
    brand = str(row.get("brand", "")).strip().lower()
    link = str(row.get("link", "")).strip().lower()
    image_link = str(row.get("image_link", "")).strip().lower()

    if pid in SAMPLE_IDS:
        return f"id '{row.get('id')}' matches a known Google sample feed id"
    if title in SAMPLE_TITLES:
        return f"title '{row.get('title')}' matches a known Google sample feed title"
    if gtin in SAMPLE_GTINS:
        return f"gtin '{row.get('gtin')}' matches a known Google sample/placeholder gtin"
    if brand in SAMPLE_BRANDS:
        return f"brand '{row.get('brand')}' matches Google's own sample feed brand"
    if any(s in link for s in SAMPLE_LINK_SUBSTRINGS):
        return f"link '{row.get('link')}' points at a placeholder domain (example.com)"
    if any(s in image_link for s in SAMPLE_LINK_SUBSTRINGS):
        return f"image_link '{row.get('image_link')}' points at a placeholder domain (example.com)"
    return None


def validate_row(row):
    """Returns (is_valid, reasons). A row is valid only if every required
    field is present AND non-blank, AND it has at least one working product
    identifier (gtin with a valid checksum, or a non-blank mpn as fallback).
    Never silently coerces a missing value to a default -- every exclusion
    gets a specific, loggable reason."""
    reasons = []
    for field in REQUIRED_FIELDS:
        value = row.get(field)
        if value is None or str(value).strip() == "":
            reasons.append(f"missing required field '{field}'")

    if not str(row.get("brand", "")).strip():
        reasons.append("missing required field 'brand'")

    gtin = row.get("gtin") or ""
    mpn = row.get("mpn") or ""
    if not gtin and not mpn:
        reasons.append("no gtin or mpn -- product has no unique identifier")
    elif gtin and not gtin_checksum_valid(gtin):
        reasons.append(f"gtin '{gtin}' fails GS1 checksum validation")

    try:
        price = float(row.get("price_amount", "nan"))
        if price <= 0:
            reasons.append(f"price '{row.get('price_amount')}' is not a positive number")
    except (TypeError, ValueError):
        reasons.append(f"price '{row.get('price_amount')}' is not numeric")

    return (len(reasons) == 0, reasons)


# ---------------------------------------------------------------------------
# Input adapter 1: Shopify "Export products" CSV (Layer 1 / offline testing)
# ---------------------------------------------------------------------------
def load_products_from_csv(path):
    """Parses a Shopify product export CSV into ProductRow objects, one per
    variant. Shopify's export repeats the handle across variant/image rows
    and leaves Title/Body (HTML)/Vendor blank on every row after a product's
    first -- both are carried forward here by tracking the last-seen values
    per handle, matching how Shopify's own bulk editor interprets the file."""
    rows = []
    carry = {}
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            handle = raw.get("Handle", "").strip()
            if not handle:
                continue
            if raw.get("Title", "").strip():
                carry[handle] = {
                    "title": raw["Title"].strip(),
                    "body_html": raw.get("Body (HTML)", "") or "",
                    "vendor": raw.get("Vendor", "").strip(),
                }
            base = carry.get(handle, {"title": "", "body_html": "", "vendor": ""})

            sku = raw.get("Variant SKU", "").strip()
            if not sku:
                continue  # image-only / option-only continuation row, not an offer
            if (raw.get("Status") or "active").strip().lower() != "active":
                continue  # draft/archived products are never Merchant Center eligible

            barcode = (raw.get("Variant Barcode") or "").strip()
            image = (raw.get("Image Src") or "").strip()
            qty_raw = (raw.get("Variant Inventory Qty") or "").strip()
            try:
                qty = int(float(qty_raw)) if qty_raw else 0
            except ValueError:
                qty = 0

            rows.append(ProductRow(
                handle=handle,
                id=sku,
                title=base["title"],
                description=strip_html(base["body_html"]),
                link=f"https://{STORE_DOMAIN_PUBLIC}/products/{quote(handle)}?variant_sku={quote(sku)}",
                image_link=image,
                price_amount=raw.get("Variant Price", "").strip(),
                price=f"{raw.get('Variant Price', '').strip()} EUR" if raw.get("Variant Price", "").strip() else "",
                availability="in_stock" if qty > 0 else "out_of_stock",
                brand=base["vendor"],
                condition="new",
                gtin=barcode if gtin_checksum_valid(barcode) else "",
                mpn=sku,
                item_group_id=handle,
            ))
    return rows


# ---------------------------------------------------------------------------
# Input adapter 2: Shopify Admin GraphQL API (Layer 2 / live daily sync)
# Same OAuth client-credentials pattern as cerda-sync/sync_angel_cerda.py --
# Dev Dashboard apps (created since Jan 2026) expose no static Admin API
# token, so every run exchanges Client ID/Secret for a short-lived token.
# NOT exercised against production in this build -- covered by
# tests/test_build_feed.py with a mocked GraphQL response instead.
# ---------------------------------------------------------------------------
_cached_token = None


def _get_access_token(shop_domain, client_id, client_secret):
    global _cached_token
    if _cached_token:
        return _cached_token
    resp = requests.post(
        f"https://{shop_domain}/admin/oauth/access_token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    resp.raise_for_status()
    _cached_token = resp.json()["access_token"]
    return _cached_token


PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        handle
        title
        descriptionHtml
        vendor
        status
        featuredImage { url }
        variants(first: 100) {
          edges {
            node {
              sku
              price
              barcode
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""


def load_products_from_shopify_api(shop_domain, client_id, client_secret):
    """Live pull: paginates products(status:active), flattens to one
    ProductRow per variant. Mirrors load_products_from_csv()'s output shape
    exactly so the rest of the pipeline can't tell which adapter ran."""
    token = _get_access_token(shop_domain, client_id, client_secret)
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
    rows = []
    cursor = None
    while True:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": PRODUCTS_QUERY, "variables": {"cursor": cursor}},
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
            description = strip_html(node.get("descriptionHtml") or "")
            image = (node.get("featuredImage") or {}).get("url", "") or ""
            for vedge in node["variants"]["edges"]:
                v = vedge["node"]
                sku = (v.get("sku") or "").strip()
                if not sku:
                    continue
                barcode = (v.get("barcode") or "").strip()
                qty = v.get("inventoryQuantity") or 0
                price_amount = str(v.get("price") or "")
                rows.append(ProductRow(
                    handle=handle,
                    id=sku,
                    title=node["title"],
                    description=description,
                    link=f"https://{STORE_DOMAIN_PUBLIC}/products/{quote(handle)}?variant_sku={quote(sku)}",
                    image_link=image,
                    price_amount=price_amount,
                    price=f"{price_amount} EUR" if price_amount else "",
                    availability="in_stock" if qty > 0 else "out_of_stock",
                    brand=(node.get("vendor") or "").strip(),
                    condition="new",
                    gtin=barcode if gtin_checksum_valid(barcode) else "",
                    mpn=sku,
                    item_group_id=handle,
                ))
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return rows


# ---------------------------------------------------------------------------
# Pipeline: validate -> reject samples -> write feed + exclusions + report
# ---------------------------------------------------------------------------
FEED_COLUMNS = [
    "id", "title", "description", "link", "image_link", "availability",
    "price", "brand", "condition", "gtin", "mpn", "item_group_id",
]


def run_pipeline(rows, out_basename, run_label):
    accepted, excluded, sample_rejected, empty_description = [], [], [], []

    for row in rows:
        sample_reason = is_known_sample_value(row)
        if sample_reason:
            sample_rejected.append((row, sample_reason))
            continue

        is_valid, reasons = validate_row(row)
        if not is_valid:
            # A row excluded ONLY for a blank description (every other
            # required field present) is a distinct, actionable case --
            # "write copy for this product" -- not a generic data problem.
            # Still excluded from the feed either way (Google would reject a
            # description-less offer too), but tracked and reported
            # separately so it doesn't get lost among real validation bugs.
            if reasons == ["missing required field 'description'"]:
                empty_description.append(row)
            else:
                excluded.append((row, reasons))
            continue

        accepted.append(row)

    feed_csv_path = DATA_DIR / f"{out_basename}.csv"
    feed_txt_path = DATA_DIR / f"{out_basename}.txt"
    for path, delim in ((feed_csv_path, ","), (feed_txt_path, "\t")):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FEED_COLUMNS, delimiter=delim, extrasaction="ignore")
            writer.writeheader()
            for row in accepted:
                writer.writerow(row)

    exclusions_path = DATA_DIR / f"{out_basename}_exclusions.csv"
    with open(exclusions_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "title", "reason", "category"])
        for row, reasons in excluded:
            writer.writerow([row.get("id", ""), row.get("title", ""), "; ".join(reasons), "validation"])
        for row, reason in sample_rejected:
            writer.writerow([row.get("id", ""), row.get("title", ""), reason, "sample_data"])

    report_lines = [
        f"# Merchant feed build report -- {run_label}",
        "",
        f"- Rows read: {len(rows)}",
        f"- Accepted into feed: {len(accepted)}",
        f"- Excluded (validation failures): {len(excluded)}",
        f"- Rejected (known Google sample/placeholder data): {len(sample_rejected)}",
        f"- Excluded for empty body_html -- needs written content (not auto-generated): {len(empty_description)}",
        "",
    ]
    if sample_rejected:
        report_lines.append(f"## Sample-data rejects ({len(sample_rejected)}) -- root-cause guard fired")
        report_lines.append(
            "These rows matched known Google documentation sample/placeholder values "
            "(the original suspension's root cause) and were hard-rejected regardless "
            "of whether they otherwise looked valid:"
        )
        for row, reason in sample_rejected:
            report_lines.append(f"- `{row.get('id')}` {row.get('title')}: {reason}")
        report_lines.append("")
    if excluded:
        report_lines.append(f"## Validation exclusions ({len(excluded)})")
        for row, reasons in excluded:
            report_lines.append(f"- `{row.get('id') or '(no id)'}` {row.get('title') or '(no title)'}: {'; '.join(reasons)}")
        report_lines.append("")
    if empty_description:
        report_lines.append(f"## Products with genuinely empty body_html ({len(empty_description)})")
        report_lines.append(
            "Excluded from the feed for now (every other required field is present, "
            "and Google requires a description) -- these need hand-written content, "
            "not auto-generation:"
        )
        for row in empty_description:
            report_lines.append(f"- `{row.get('id')}` {row.get('title')}")
        report_lines.append("")

    report_path = REPORTS_DIR / f"{date.today().isoformat()}.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return {
        "rows_read": len(rows),
        "accepted": len(accepted),
        "excluded": len(excluded),
        "sample_rejected": len(sample_rejected),
        "empty_description": empty_description,
        "feed_csv_path": feed_csv_path,
        "feed_txt_path": feed_txt_path,
        "exclusions_path": exclusions_path,
        "report_path": report_path,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["csv", "shopify-api"], required=True)
    parser.add_argument("--csv-path", help="Path to a Shopify product export CSV (required for --source csv)")
    parser.add_argument("--out", default=None, help="Output basename under data/ (default: google_merchant_feed_<date>)")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate but do not write files (prints summary only)")
    args = parser.parse_args()

    if args.source == "csv":
        if not args.csv_path:
            parser.error("--csv-path is required when --source csv")
        rows = load_products_from_csv(args.csv_path)
        run_label = f"csv:{args.csv_path} on {date.today().isoformat()}"
    else:
        shop_domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
        client_id = os.environ.get("SHOPIFY_CLIENT_ID")
        client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
        if not all([shop_domain, client_id, client_secret]):
            print("ERROR: SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET must all be set for --source shopify-api", file=sys.stderr)
            sys.exit(1)
        rows = load_products_from_shopify_api(shop_domain, client_id, client_secret)
        run_label = f"shopify-api:{shop_domain} on {date.today().isoformat()}"

    out_basename = args.out or f"google_merchant_feed_{date.today().isoformat()}"

    if args.dry_run:
        accepted, excluded, sample_rejected = 0, 0, 0
        for row in rows:
            if is_known_sample_value(row):
                sample_rejected += 1
            elif not validate_row(row)[0]:
                excluded += 1
            else:
                accepted += 1
        print(f"[dry-run] rows_read={len(rows)} accepted={accepted} excluded={excluded} sample_rejected={sample_rejected}")
        return

    stats = run_pipeline(rows, out_basename, run_label)
    print(
        f"rows_read={stats['rows_read']} accepted={stats['accepted']} "
        f"excluded={stats['excluded']} sample_rejected={stats['sample_rejected']} "
        f"empty_description={len(stats['empty_description'])}"
    )
    print(f"Feed written to {stats['feed_csv_path']} and {stats['feed_txt_path']}")
    print(f"Exclusions logged to {stats['exclusions_path']}")
    print(f"Report written to {stats['report_path']}")


if __name__ == "__main__":
    main()
