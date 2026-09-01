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

--market <key>            (--source shopify-api only) Selects which market's
                           locale to pull title/description in and which
                           storefront link prefix to use -- see MARKETS.
                           Each market is its own Merchant Center data
                           source/output file. Default: de.

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

# ---------------------------------------------------------------------------
# Market targets this pipeline builds feeds for. Each key is a distinct
# Merchant Center data source: its own scheduled-fetch URL (own --out
# basename), and its own "target country" + "language" pair set manually in
# the MC wizard (see README) -- this pipeline does not set country/language
# in the feed itself, it only selects which locale's title/description to
# pull, matching whichever MC data source(s) will fetch that output file.
#
# "countries" is documentation of the intended MC target(s) for that data
# source, not a filter: the Shopify query below always pulls the full active
# catalog regardless of market, because Shopify Markets (Settings > Markets)
# already governs which countries can actually buy each product. DE/AT/LU
# share identical (German) content today because all three MC data sources
# point at the same feed file -- "de" stays a single build. Belgium needs
# its own two builds (be-fr, be-nl) because it's the first market this feed
# targets that isn't German-language.
#
# France is deliberately absent. Shopify Markets has France set to Draft
# (not selling) as of 2026-09-01 -- do not add a France entry here, and do
# not point a France-targeted MC data source at the be-fr build (be-fr's
# French content is for Belgian French-speakers; its MC data source must be
# configured with target country Belgium, not France). Re-add France only
# after confirming its Market is Active again.
# ---------------------------------------------------------------------------
MARKETS = {
    "de": {"locale": "de", "link_prefix": "", "countries": ["Germany", "Austria", "Luxembourg"]},
    "be-fr": {"locale": "fr", "link_prefix": "/fr", "countries": ["Belgium"]},
    "be-nl": {"locale": "nl", "link_prefix": "/nl", "countries": ["Belgium"]},
}

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
query($cursor: String, $locale: String!) {
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
        translations(locale: $locale) { key value }
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


def load_products_from_shopify_api(shop_domain, client_id, client_secret, market="de"):
    """Live pull: paginates products(status:active), flattens to one
    ProductRow per variant. Mirrors load_products_from_csv()'s output shape
    exactly so the rest of the pipeline can't tell which adapter ran.

    `market` selects a MARKETS entry (locale + link_prefix). Defaults to
    "de" so existing callers keep today's exact behavior.

    The feed's `link` must match the locale being pulled -- confirmed via
    hreflang: the storefront's bare URL is hreflang="de" (German default),
    with /fr/, /nl/, /en/ prefixes for the other locales. Publishing German
    title/description under a bare (German) link, or French/Dutch
    title/description under an /fr/ or /nl/ link, keeps the feed's language
    matching the page it links to -- see MARKETS' link_prefix per market.

    No fallback to another locale on a missing translation for the
    requested market: a product missing that locale's title and/or
    body_html translation is flagged via ProductRow['translation_missing']
    instead of silently publishing blank or mismatched-language content.
    run_pipeline() excludes these and reports them as a distinct category
    (translation gap) separate from ordinary validation failures or from
    "genuinely no content at all" (empty_description, the DE case)."""
    if market not in MARKETS:
        raise ValueError(f"Unknown market '{market}' -- choices are {sorted(MARKETS)}")
    locale = MARKETS[market]["locale"]
    link_prefix = MARKETS[market]["link_prefix"]

    token = _get_access_token(shop_domain, client_id, client_secret)
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
    rows = []
    cursor = None
    while True:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": PRODUCTS_QUERY, "variables": {"cursor": cursor, "locale": locale}},
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
            tr = {t["key"]: t["value"] for t in node.get("translations") or []}
            title = tr.get("title") or ""
            description = strip_html(tr.get("body_html") or "")
            translation_missing = not tr.get("title") or not tr.get("body_html")
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
                    title=title,
                    description=description,
                    link=f"https://{STORE_DOMAIN_PUBLIC}{link_prefix}/products/{quote(handle)}?variant_sku={quote(sku)}",
                    image_link=image,
                    price_amount=price_amount,
                    price=f"{price_amount} EUR" if price_amount else "",
                    availability="in_stock" if qty > 0 else "out_of_stock",
                    brand=(node.get("vendor") or "").strip(),
                    condition="new",
                    gtin=barcode if gtin_checksum_valid(barcode) else "",
                    mpn=sku,
                    item_group_id=handle,
                    translation_missing=translation_missing,
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


def run_pipeline(rows, out_basename, run_label, market="de"):
    accepted, excluded, sample_rejected, empty_description, missing_translation = [], [], [], [], []

    for row in rows:
        sample_reason = is_known_sample_value(row)
        if sample_reason:
            sample_rejected.append((row, sample_reason))
            continue

        if row.get("translation_missing"):
            # Distinct from empty_description: the German catalog copy
            # exists, it just hasn't been translated into this market's
            # locale yet -- an actionable translation-backlog item, not a
            # generic validation bug or missing-content case. Never
            # silently published blank/partial under this market's feed.
            missing_translation.append(row)
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
        for row in missing_translation:
            writer.writerow([row.get("id", ""), row.get("handle", ""), "title and/or body_html not translated into this market's locale", "missing_translation"])

    report_lines = [
        f"# Merchant feed build report -- {run_label}",
        "",
        f"- Rows read: {len(rows)}",
        f"- Accepted into feed: {len(accepted)}",
        f"- Excluded (validation failures): {len(excluded)}",
        f"- Rejected (known Google sample/placeholder data): {len(sample_rejected)}",
        f"- Excluded for empty body_html -- needs written content (not auto-generated): {len(empty_description)}",
        f"- Excluded for missing locale translation (title and/or body_html not yet translated): {len(missing_translation)}",
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
    if missing_translation:
        report_lines.append(f"## Products missing this market's translation ({len(missing_translation)})")
        report_lines.append(
            "Excluded from this market's feed -- title and/or body_html has not been "
            "translated into this market's locale yet (German content may still exist; "
            "not the same as empty_description):"
        )
        seen_handles = set()
        for row in missing_translation:
            handle = row.get("handle") or "(no handle)"
            if handle in seen_handles:
                continue  # one line per product, not per variant
            seen_handles.add(handle)
            report_lines.append(f"- `{handle}` (sku `{row.get('id')}`)")
        report_lines.append("")

    # DE keeps its exact existing filename (send_weekly_report.py looks up
    # reports/YYYY-MM-DD.md verbatim for the DE digest) -- only non-DE
    # markets get a suffix, so running multiple markets on the same day
    # can't silently overwrite each other's report (or DE's).
    report_suffix = "" if market == "de" else f"_{market}"
    report_path = REPORTS_DIR / f"{date.today().isoformat()}{report_suffix}.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return {
        "rows_read": len(rows),
        "accepted": len(accepted),
        "excluded": len(excluded),
        "sample_rejected": len(sample_rejected),
        "empty_description": empty_description,
        "missing_translation": missing_translation,
        "feed_csv_path": feed_csv_path,
        "feed_txt_path": feed_txt_path,
        "exclusions_path": exclusions_path,
        "report_path": report_path,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["csv", "shopify-api"], required=True)
    parser.add_argument("--csv-path", help="Path to a Shopify product export CSV (required for --source csv)")
    parser.add_argument("--market", choices=sorted(MARKETS), default="de",
                         help="Target market/locale to build for (--source shopify-api only; see MARKETS). Default: de")
    parser.add_argument("--out", default=None, help="Output basename under data/ (default: google_merchant_feed_<date>)")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate but do not write files (prints summary only)")
    args = parser.parse_args()

    if args.source == "csv":
        if not args.csv_path:
            parser.error("--csv-path is required when --source csv")
        if args.market != "de":
            parser.error("--market is not supported with --source csv (the export CSV carries no locale selection)")
        rows = load_products_from_csv(args.csv_path)
        run_label = f"csv:{args.csv_path} on {date.today().isoformat()}"
    else:
        shop_domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
        client_id = os.environ.get("SHOPIFY_CLIENT_ID")
        client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
        if not all([shop_domain, client_id, client_secret]):
            print("ERROR: SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET must all be set for --source shopify-api", file=sys.stderr)
            sys.exit(1)
        rows = load_products_from_shopify_api(shop_domain, client_id, client_secret, market=args.market)
        run_label = f"shopify-api:{shop_domain} market={args.market} on {date.today().isoformat()}"

    out_basename = args.out or f"google_merchant_feed_{date.today().isoformat()}"

    if args.dry_run:
        accepted, excluded, sample_rejected, missing_translation = 0, 0, 0, 0
        for row in rows:
            if is_known_sample_value(row):
                sample_rejected += 1
            elif row.get("translation_missing"):
                missing_translation += 1
            elif not validate_row(row)[0]:
                excluded += 1
            else:
                accepted += 1
        print(
            f"[dry-run] rows_read={len(rows)} accepted={accepted} excluded={excluded} "
            f"sample_rejected={sample_rejected} missing_translation={missing_translation}"
        )
        return

    stats = run_pipeline(rows, out_basename, run_label, market=args.market)
    print(
        f"rows_read={stats['rows_read']} accepted={stats['accepted']} "
        f"excluded={stats['excluded']} sample_rejected={stats['sample_rejected']} "
        f"empty_description={len(stats['empty_description'])} "
        f"missing_translation={len(stats['missing_translation'])}"
    )
    print(f"Feed written to {stats['feed_csv_path']} and {stats['feed_txt_path']}")
    print(f"Exclusions logged to {stats['exclusions_path']}")
    print(f"Report written to {stats['report_path']}")


if __name__ == "__main__":
    main()
