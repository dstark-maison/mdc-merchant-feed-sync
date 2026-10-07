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
from functools import lru_cache
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

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
# France's Market went ACTIVE (re-verified live 2026-09-19; was Draft as of
# 2026-09-01, when this guard was first written). France, Belgium, and
# Netherlands all share the identical MarketWebPresence (domain
# www.maisondecocon.com, same /fr/ and /nl/ subfolders) -- so French content
# for France is byte-identical to French content for Belgium, and "be-fr"
# now targets both countries under its existing feed label/build rather than
# needing a second one. Adding France to the BE-FR Merchant Center data
# source's target-country list is a manual dashboard step (see README) --
# this pipeline never sets MC country targeting itself.
#
# Netherlands has no feed of its own: Belgium (Dutch) and Netherlands (Dutch)
# produce byte-identical rows -- the product links are www.maisondecocon.com/nl
# for both, because every non-DE market shares one web presence and Shopify
# picks the market from the visitor's country, not from the URL -- and likewise
# English (Germany/Austria/Belgium/Luxembourg/France) and Netherlands (English).
# So "be-nl" and "en" also carry Netherlands as a target country, and the
# separate "nl" / "en-nl" builds (and Merchant Center sources) were retired.
#
# "primary": True (the "en" entry) marks a market whose
# locale is this shop's PRIMARY locale (confirmed via shopLocales:
# en.primary == true). Shopify never stores a translations() record for
# the primary locale -- translations(locale: "en") returns [] for every
# product, always, by design, not because content is missing. Primary-
# locale content lives on the resource's own base `title`/`descriptionHtml`
# fields instead. The loader branches on this flag: primary markets read
# those base fields directly and never set translation_missing (there is
# no "translation" to be missing for the shop's own native-language
# content -- a blank base field is a genuine content gap and flows through
# the ordinary validate_row/empty_description path, same as CSV-sourced
# rows already do). Getting this wrong would silently ship an empty feed:
# every row would read translation_missing=True and 0 products would be
# accepted -- verified this would happen before adding "en" here, and the
# same is true for any other locale=="en" market, including any future one.
# ---------------------------------------------------------------------------
MARKETS = {
    "de": {"locale": "de", "link_prefix": "", "countries": ["Germany", "Austria", "Luxembourg"]},
    "be-fr": {"locale": "fr", "link_prefix": "/fr", "countries": ["Belgium", "France"]},
    "be-nl": {"locale": "nl", "link_prefix": "/nl", "countries": ["Belgium", "Netherlands"]},
    "en": {"locale": "en", "link_prefix": "/en", "countries": ["Germany", "Austria", "Belgium", "Luxembourg", "France", "Netherlands"], "primary": True},
}

# ---------------------------------------------------------------------------
# Merchant Center `shipping_label` per product. Shipping cost and delivery time
# live ONLY in the account-level Merchant Center shipping services (filtered by
# label) -- this pipeline emits no per-row `shipping` cell.
# THIS IS THE ONLY PLACE the vendor list lives -- to onboard a vendor, add one
# entry here AND create its GMC service. Keys must match Shopify's `vendor`
# string exactly. Unmapped vendors get DEFAULT_SHIPPING_LABEL (a GMC service
# filtered to that label, 15 EUR flat).
# ---------------------------------------------------------------------------
VENDOR_SHIPPING_LABELS = {
    "Boomba Bamboo": "std_9",
    "MoST Blankets": "std_990",
    "Coco & Cici": "std_10",
    "VIVARAISE": "std_15",
}
DEFAULT_SHIPPING_LABEL = "std_default"

# SalesFever has two Shopify delivery profiles: "SalesFever — Bulky"
# (DeliveryProfile/141017874765, 12 beds) and "SalesFever — Small"
# (DeliveryProfile/141024461133, the 6 Bed Benches, 19.90 EUR DE / 79 EUR
# AT-BE-FR-LU-NL). Confirmed 2026-09-26 against the Orderchamp "Supported
# Countries" table for the Storage Bed Bench (no per-unit surcharge). The
# theme's freight_tier metafield now mirrors this: tier 8 = Bulky (119/239),
# tier 9 = Small (19.90/79) -- see
# maison-de-cocon-shopify-theme/snippets/custom-freight-tier-price.liquid.
# If either delivery profile's rate changes, update both that snippet and
# the SALESFEVER_RATES-equivalent GMC service, not just here.
SALESFEVER_SMALL_TYPES = {"Bed Benches"}


def shipping_label_for(vendor, product_type=""):
    """Merchant Center shipping_label for a product; unmapped vendors get
    DEFAULT_SHIPPING_LABEL so no product is ever unlabelled."""
    vendor = (vendor or "").strip()
    if vendor == "SalesFever":
        return "sf_small" if (product_type or "").strip() in SALESFEVER_SMALL_TYPES else "sf_bulky"
    return VENDOR_SHIPPING_LABELS.get(vendor, DEFAULT_SHIPPING_LABEL)


# ---------------------------------------------------------------------------
# Brand mapping (GMC feed only). brands.yaml is the single source of truth for
# which Shopify vendors are consumer brands (brand attribute + title prefix)
# and which are suppliers/white-label (brand = "Maison de Cocon", title
# untouched). Applied once, in run_pipeline(), AFTER both input adapters, so
# the CSV and GraphQL paths and every market/language get the identical result.
# The adapters still emit brand=vendor and the raw title: idealo_feed.py reuses
# them and keys its shipping/delivery tables on that vendor string.
# ---------------------------------------------------------------------------
BRANDS_FILE = ROOT / "brands.yaml"
MAX_TITLE_LEN = 150
_TITLE_SEGMENT_SPLIT = re.compile(r"(\s+[-–|]\s+|,\s+)")


@lru_cache(maxsize=None)
def load_brand_config(path=BRANDS_FILE):
    """Parses and validates brands.yaml -> {"default": str, "consumer":
    {vendor: brand}, "white_label": set(vendors), "passthrough": set(vendors)}. Fails loudly on a
    malformed file or a vendor listed in both sections -- a silent
    misconfiguration here would ship wrong brands to Google."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    default = str(raw.get("default_brand") or "").strip()
    if not default:
        raise ValueError(f"{path}: default_brand is required")
    consumer = {str(k).strip(): str(v).strip() for k, v in (raw.get("consumer_brands") or {}).items()}
    white_label = {str(v).strip() for v in (raw.get("white_label") or [])}
    passthrough = {str(v).strip() for v in (raw.get("passthrough") or [])}
    if any(not b for b in consumer.values()):
        raise ValueError(f"{path}: consumer_brands entries need a non-empty brand")
    sections = [set(consumer), white_label, passthrough]
    both = set().union(*(a & b for i, a in enumerate(sections) for b in sections[i + 1:]))
    if both:
        raise ValueError(f"{path}: vendor(s) listed in more than one section: {sorted(both)}")
    return {"default": default, "consumer": consumer, "white_label": white_label, "passthrough": passthrough}


def brand_for_vendor(vendor, config=None):
    """-> (brand, is_consumer_brand, is_unknown) for a Shopify vendor string.
    Unknown vendors fall back to the default brand, never to the vendor name,
    and is_unknown=True so the build report can flag them. "passthrough"
    vendors keep their vendor string as the brand (brand = vendor, title not prefixed).
    An EMPTY vendor yields brand "" (flagged): the row then fails validation
    on 'brand' and is skipped, as it always has."""
    config = config or load_brand_config()
    vendor = (vendor or "").strip()
    if not vendor:
        return "", False, True
    if vendor in config["passthrough"]:
        return vendor, False, False
    if vendor in config["consumer"]:
        return config["consumer"][vendor], True, False
    if vendor in config["white_label"]:
        return config["default"], False, False
    return config["default"], False, True


def _brand_key(text):
    """Case/punctuation/space-insensitive key so "Coco&Cici", "COCO & CICI"
    and "coco-cici" all count as containing the brand "Coco & Cici"."""
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def _drop_brand_overlap(title, brand):
    """If the title starts with the last word(s) of the brand (e.g. brand
    "Boomba Bamboo", title "Bamboo Fitted Sheet"), drops those words from the
    title so the brand prefix doesn't repeat them: "Boomba Bamboo Fitted
    Sheet". Case-insensitive, ignores punctuation and TM/R marks; whole words
    only and the same language only ("Bambus" never matches "Bamboo"). The
    longest overlap wins; a title that is nothing but the overlap is kept."""
    t_words, b_words = title.split(), brand.split()
    for k in range(min(len(b_words) - 1, len(t_words) - 1), 0, -1):
        if [_brand_key(w) for w in t_words[:k]] == [_brand_key(w) for w in b_words[-k:]]:
            return " ".join(t_words[k:])
    return title


def title_with_brand(title, brand, max_len=MAX_TITLE_LEN):
    """"{brand} {title}", unless the title already contains the brand. Over
    max_len, only trailing attributes are shortened -- whole trailing
    segments (split on " - ", " | ", ", ") first, then a word-boundary cut --
    so the brand and the leading product term are never touched. The brand
    stays untranslated; the title is already in the market's language.
    A title that starts with the brand's trailing word(s) has them merged
    (see _drop_brand_overlap)."""
    title = (title or "").strip()
    if not title or _brand_key(brand) in _brand_key(title):
        return title
    title = _drop_brand_overlap(title, brand)
    new = f"{brand} {title}".strip()
    if len(new) <= max_len:
        return new
    parts = _TITLE_SEGMENT_SPLIT.split(new)  # text, sep, text, sep, ...
    while len(parts) > 1 and len("".join(parts)) > max_len:
        del parts[-2:]  # drop the last separator + segment
    new = "".join(parts)
    if len(new) > max_len:
        head = new[:max_len + 1]
        new = head.rsplit(" ", 1)[0] if " " in head else new[:max_len]
    return new.rstrip(" -–|,/+&")


def apply_brand_mapping(row, config=None):
    """Returns (new_row, is_unknown_vendor). Never mutates `row`. Vendor is
    read from row["vendor"] if present, else row["brand"] (adapters emit
    brand=vendor). The original vendor is preserved in new_row["vendor"]."""
    config = config or load_brand_config()
    vendor = (row.get("vendor") if row.get("vendor") is not None else row.get("brand")) or ""
    brand, is_consumer, is_unknown = brand_for_vendor(vendor, config)
    new = ProductRow(row)
    new["vendor"] = vendor.strip()
    new["brand"] = brand
    if is_consumer:
        new["title"] = title_with_brand(row.get("title", ""), brand)
    return new, is_unknown


# Google requires gender + age_group on Apparel & Accessories offers
# (support.google.com/merchants/answer/6324479,
# support.google.com/merchants/answer/6324463). The real source of truth is
# each variant's own mm-google-shopping.gender/age_group metafield (set
# directly on the product's variants in Shopify) -- this table is only a
# fallback for a variant that has neither set, keyed by product_type the
# same way SALESFEVER_SMALL_TYPES/CATEGORY_PATHS_DE (idealo_feed.py) are.
# Every current Nightgowns/Pyjamas offer is women's sleepwear ("Ladies" in
# the title), so the fallback is safe today, but a future unisex or
# children's item in either type should get its own variant metafields
# rather than silently inheriting this default -- add a real metafield
# rather than widening this table.
PRODUCT_TYPE_GENDER_FALLBACK = {"Nightgowns": "female", "Pyjamas": "female"}
PRODUCT_TYPE_AGE_GROUP_FALLBACK = {"Nightgowns": "adult", "Pyjamas": "adult"}


def gender_for(product_type, variant_gender=""):
    """gender for one offer: the variant's own metafield value if set,
    else the product_type fallback, else "" (e.g. Sleep Masks, or any
    non-apparel type -- never guessed for a type not in the table)."""
    variant_gender = (variant_gender or "").strip()
    if variant_gender:
        return variant_gender
    return PRODUCT_TYPE_GENDER_FALLBACK.get((product_type or "").strip(), "")


def age_group_for(product_type, variant_age_group=""):
    """age_group for one offer: same precedence as gender_for."""
    variant_age_group = (variant_age_group or "").strip()
    if variant_age_group:
        return variant_age_group
    return PRODUCT_TYPE_AGE_GROUP_FALLBACK.get((product_type or "").strip(), "")


# EU 2019/771 gives every EU consumer a minimum 2-year statutory conformity
# guarantee regardless of what a merchant's own return policy says. This is
# informational metadata on rows only. Return-policy coverage is still handled
# at the ACCOUNT level in Merchant Center (Verified "Standard for Germany"
# policy); this pipeline emits no per-row return-policy column. Shipping cost
# and delivery time are account-level too, selected by shipping_label.
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

# Google's cap on additional images per offer (support.google.com/merchants/answer/6324370).
MAX_ADDITIONAL_IMAGES = 10


class ProductRow(dict):
    """A single feed-eligible offer: one Shopify variant flattened to the
    field names Google's product feed spec uses. Plain dict subclass -- no
    behavior, just a documented shape so callers don't have to guess keys:
    id, title, description, link, image_link, additional_image_link, price,
    availability, brand, condition, gtin, mpn, item_group_id, color, size,
    material, gender, age_group, handle, vendor (handle and vendor are feed-internal, stripped
    before writing -- kept only for grouping/debugging). additional_image_link is optional --
    Google's own format for it in a tab/comma-delimited feed is a single
    column holding up to 10 comma-separated URLs
    (support.google.com/merchants/answer/6324370), not a repeated column, so
    it is built and stored as one pre-joined string, same as every other
    column here.

    color/size/material are populated ONLY from data that genuinely exists
    on the product in Shopify. color: the product-level shopify.color-pattern
    category metafield (resolved to its metaobject's display label, e.g.
    "Hazel") if set, else a real "Color"/"Colour" variant option -- CSV rows
    only ever get the option (no metafields in a Shopify export). size: a
    real "Size" variant option (matching any option name containing "size",
    e.g. "Choose your size"), with a leading quantity/noun prefix stripped
    (see strip_size_quantity_prefix). material: the maison_seo.fabric
    metafield. A product with no Color option and no color-pattern metafield
    (some Casilin and Boomba Bedding Set products) gets color="" rather than
    a value parsed out of its title -- never invented, never defaulted. There
    is deliberately no `pattern` field: no source of pattern data exists
    anywhere in this catalog's Shopify data (no option, metafield, or tag),
    and defaulting one in (e.g. "solid") would be fabricated data on a feed
    for an account with a prior Misrepresentation suspension -- see the
    module docstring's sample-data guard for why that risk is taken
    seriously here.

    gender/age_group: a real variant-level mm-google-shopping.gender /
    age_group metafield takes priority when set; PRODUCT_TYPE_GENDER_
    FALLBACK / PRODUCT_TYPE_AGE_GROUP_FALLBACK (see gender_for/age_group_for)
    only fires when a variant has neither, and only for product types in
    that table (e.g. Nightgowns, Pyjamas) -- "" for every other type, same
    never-invented policy as color/size/material."""


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


# Barcodes used as "no EAN yet" placeholders by suppliers (VIVARAISE: 3210800000000). They are never real GTINs:
# the feed omits gtin for them and relies on brand + mpn (= SKU). Today this value also fails the GS1 checksum, but
# that is a coincidence -- list placeholders here so they stay out even if one ever validates.
PLACEHOLDER_BARCODES = {"3210800000000"}


def usable_gtin(barcode):
    """Barcode as a feed gtin, or "" when it is empty, a known placeholder, or fails the GS1 checksum."""
    barcode = (barcode or "").strip()
    if barcode in PLACEHOLDER_BARCODES:
        return ""
    return barcode if gtin_checksum_valid(barcode) else ""


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
    additional_image_link = str(row.get("additional_image_link", "")).strip().lower()

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
    if any(s in additional_image_link for s in SAMPLE_LINK_SUBSTRINGS):
        return f"additional_image_link '{row.get('additional_image_link')}' points at a placeholder domain (example.com)"
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
def _collect_csv_images(raw_rows):
    """Collects every distinct Image Src per handle across ALL rows for that
    handle -- including the image-only continuation rows load_products_from_csv
    otherwise skips (no Variant SKU) -- ordered by Shopify's own Image
    Position column where present, falling back to file order when it's
    blank/non-numeric. Used to build additional_image_link: the row's own
    image_link is excluded from the list by the caller, not here, since this
    function doesn't know per-variant which image that row already used."""
    positioned = {}
    for idx, raw in enumerate(raw_rows):
        handle = (raw.get("Handle") or "").strip()
        src = (raw.get("Image Src") or "").strip()
        if not handle or not src:
            continue
        try:
            pos = int(float(raw.get("Image Position") or ""))
        except ValueError:
            pos = idx
        positioned.setdefault(handle, []).append((pos, src))

    images_by_handle = {}
    for handle, entries in positioned.items():
        entries.sort(key=lambda entry: entry[0])
        seen, ordered = set(), []
        for _, src in entries:
            if src in seen:
                continue
            seen.add(src)
            ordered.append(src)
        images_by_handle[handle] = ordered
    return images_by_handle


def _csv_option_value(raw, option_name):
    """Looks up a named Shopify product option's value for one CSV row.
    Shopify's product export carries up to 3 option columns per variant row
    as Option<N> Name / Option<N> Value pairs -- this checks all 3 slots,
    case-insensitively, matching an option whose name CONTAINS option_name
    rather than requiring an exact match (e.g. a real option named "Choose
    your size" matches option_name="size", same as one literally named
    "Size"), and returns "" if that product simply doesn't have a matching
    option (never falls back to parsing the title/handle)."""
    target = option_name.strip().lower()
    for i in (1, 2, 3):
        name = (raw.get(f"Option{i} Name") or "").strip().lower()
        if target in name:
            return (raw.get(f"Option{i} Value") or "").strip()
    return ""


def _color_pattern_label(color_pattern_metafield):
    """Resolves the shopify.color-pattern category metafield (a
    list.metaobject_reference to Shopify's standard "Color" metaobject
    definition) to its human-readable display name(s), e.g. "Hazel",
    "Off White" -- via the metafield's own `references` connection, which
    Shopify resolves inline (no second round-trip query needed). Multiple
    references (rare -- a genuinely multi-color product) are joined with
    "/". Returns "" if the metafield is absent or empty, so the caller can
    fall back to a real "Color"/"Colour" variant option."""
    if not color_pattern_metafield:
        return ""
    labels = []
    for node in (color_pattern_metafield.get("references") or {}).get("nodes") or []:
        value = (node.get("field") or {}).get("value")
        if value:
            labels.append(value)
    return "/".join(labels)


# A leading "<quantity> <noun(s)> " phrase on an otherwise-dimensional size
# value, e.g. "1 pillowcase 40x80" or "2 pillowcases 60x70" -- Google wants
# just the dimension ("40x80"), not the quantity/noun Shopify's option value
# happens to be phrased with. Matches the shortest leading run of digits +
# whitespace + non-digit text that is immediately followed by a digit (the
# real dimension token starting); strip_size_quantity_prefix then rejects a
# match whose non-digit run is nothing but a bare "x"/"×" dimension
# separator (e.g. "200 x 200 cm" is a real dimension, not a quantity+noun
# prefix, and must be left alone). Never touches a value that already
# starts with a dimension with no space before it (e.g. "200x200 + 2
# pillowcases 60x70") or one with no digit in it at all (e.g. "M / L",
# "XL", "S") -- generic across the whole catalog, not specific to any one
# product's option wording.
SIZE_QUANTITY_PREFIX_RE = re.compile(r"^(\d+)\s+([^\d]+?)\s*(?=\d)")


def strip_size_quantity_prefix(value):
    """Strips a leading quantity+noun prefix from a Size option value when
    one is present, e.g. "1 pillowcase 40x80" -> "40x80". Returns the value
    unchanged if it doesn't match, or if the only thing between the leading
    quantity and the dimension is a bare "x"/"×" separator -- i.e. the
    value is itself a dimension like "200 x 200 cm", not a quantity+noun
    prefix on one."""
    value = value or ""
    match = SIZE_QUANTITY_PREFIX_RE.match(value)
    if not match:
        return value
    if re.fullmatch(r"[x×]", match.group(2), re.IGNORECASE):
        return value
    return value[match.end():]


def _option_value_containing(names_to_values, substring):
    """Looks up a Shopify variant option's value from a {lowercased name:
    value} dict, matching the first name that CONTAINS substring rather than
    requiring an exact match -- e.g. matches a real option literally named
    "Size" as well as one named "Choose your size". Returns "" if none
    match. Mirrors _csv_option_value's substring-matching behavior for the
    shopify-api adapter, which gets its options as a dict rather than
    Option<N> Name/Value CSV columns."""
    substring = substring.strip().lower()
    for name, value in names_to_values.items():
        if substring in name:
            return value
    return ""


def load_products_from_csv(path):
    """Parses a Shopify product export CSV into ProductRow objects, one per
    variant. Shopify's export repeats the handle across variant/image rows
    and leaves Title/Body (HTML)/Vendor blank on every row after a product's
    first -- both are carried forward here by tracking the last-seen values
    per handle, matching how Shopify's own bulk editor interprets the file."""
    with open(path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    images_by_handle = _collect_csv_images(raw_rows)

    rows = []
    carry = {}
    for raw in raw_rows:
        handle = raw.get("Handle", "").strip()
        if not handle:
            continue
        if raw.get("Title", "").strip():
            carry[handle] = {
                "title": raw["Title"].strip(),
                "body_html": raw.get("Body (HTML)", "") or "",
                "vendor": raw.get("Vendor", "").strip(),
                "product_type": (raw.get("Type") or raw.get("Product Type") or "").strip(),
            }
        base = carry.get(handle, {"title": "", "body_html": "", "vendor": "", "product_type": ""})

        sku = raw.get("Variant SKU", "").strip()
        if not sku:
            continue  # image-only / option-only continuation row, not an offer
        if (raw.get("Status") or "active").strip().lower() != "active":
            continue  # draft/archived products are never Merchant Center eligible

        barcode = (raw.get("Variant Barcode") or "").strip()
        image = (raw.get("Image Src") or "").strip()
        additional_images = [u for u in images_by_handle.get(handle, []) if u != image][:MAX_ADDITIONAL_IMAGES]
        # CSV export carries no metafields, so color-pattern's product-level
        # metaobject reference isn't available here -- only the option
        # fallback ("colo" matches both "Color" and "Colour"). The
        # shopify-api adapter is the source of truth for the real value.
        color = _csv_option_value(raw, "colo")
        size = strip_size_quantity_prefix(_csv_option_value(raw, "size"))
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
            additional_image_link=",".join(additional_images),
            price_amount=raw.get("Variant Price", "").strip(),
            price=f"{raw.get('Variant Price', '').strip()} EUR" if raw.get("Variant Price", "").strip() else "",
            availability="in_stock" if qty > 0 else "out_of_stock",
            vendor=base["vendor"],
            brand=base["vendor"],
            condition="new",
            gtin=usable_gtin(barcode),
            mpn=sku,
            item_group_id=handle,
            color=color,
            size=size,
            # Shopify's default "Export products" CSV does not include custom
            # metafields (maison_seo.fabric would need to be explicitly added
            # as its own export column, which this pipeline doesn't assume is
            # present) -- left blank for CSV-sourced rows rather than guessed.
            # The shopify-api adapter is the source of truth for material.
            material="",
            shipping_label=shipping_label_for(base["vendor"], base["product_type"]),
            # CSV export carries no metafields, so this is always the
            # product_type fallback for CSV-sourced rows -- the shopify-api
            # adapter is the source of truth for a real per-variant value.
            gender=gender_for(base["product_type"]),
            age_group=age_group_for(base["product_type"]),
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
        productType
        status
        featuredImage { url }
        images(first: 11) { nodes { url } }
        translations(locale: $locale) { key value }
        fabricMetafield: metafield(namespace: "maison_seo", key: "fabric") { value }
        colorPatternMetafield: metafield(namespace: "shopify", key: "color-pattern") {
          references(first: 5) { nodes { ... on Metaobject { field(key: "label") { value } } } }
        }
        variants(first: 100) {
          edges {
            node {
              sku
              price
              barcode
              inventoryQuantity
              selectedOptions { name value }
              genderMetafield: metafield(namespace: "mm-google-shopping", key: "gender") { value }
              ageGroupMetafield: metafield(namespace: "mm-google-shopping", key: "age_group") { value }
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
    "genuinely no content at all" (empty_description, the DE case).

    Exception: a market flagged "primary" in MARKETS (currently "en", this
    shop's primary locale) reads title/description from the product's own
    base fields instead of translations(locale: ...) -- Shopify never
    populates a translations() record for the primary locale, so that
    lookup would always return empty and every row would be wrongly
    flagged translation_missing. See the MARKETS comment for details."""
    if market not in MARKETS:
        raise ValueError(f"Unknown market '{market}' -- choices are {sorted(MARKETS)}")
    locale = MARKETS[market]["locale"]
    link_prefix = MARKETS[market]["link_prefix"]
    is_primary = MARKETS[market].get("primary", False)
    excluded_vendors = MARKETS[market].get("excluded_vendors", set())

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
            vendor = (node.get("vendor") or "").strip()
            if vendor in excluded_vendors:
                continue  # market-level vendor exclusion (no market sets one at the moment)
            if is_primary:
                title = node.get("title") or ""
                description = strip_html(node.get("descriptionHtml") or "")
                translation_missing = False  # no translation concept for the primary locale
                link_handle = handle  # primary locale's own handle IS the base handle
            else:
                tr = {t["key"]: t["value"] for t in node.get("translations") or []}
                title = tr.get("title") or ""
                description = strip_html(tr.get("body_html") or "")
                translation_missing = not tr.get("title") or not tr.get("body_html")
                # Shopify DOES return a locale-specific `handle` translation for
                # products with a localized URL slug -- use it so `link` resolves
                # directly instead of 301-redirecting through the base handle.
                # Falls back to the base handle for products with no handle
                # translation (e.g. some products keep the English slug in every
                # locale).
                link_handle = tr.get("handle") or handle
            image = (node.get("featuredImage") or {}).get("url", "") or ""
            additional_images = []
            for img in (node.get("images") or {}).get("nodes") or []:
                img_url = (img or {}).get("url") or ""
                if img_url and img_url != image and img_url not in additional_images:
                    additional_images.append(img_url)
            additional_images = additional_images[:MAX_ADDITIONAL_IMAGES]
            material = (node.get("fabricMetafield") or {}).get("value") or ""
            product_type = (node.get("productType") or "").strip()
            # Product-level (not per-variant): Shopify's standard "Color"
            # category metafield, resolved to its display label (e.g.
            # "Hazel"). Takes priority over a variant option below when
            # present -- Google requires `color` for Apparel offers in
            # DE/FR, and this category metafield is the source of truth
            # set on the product (Phase 1), not a per-variant choice.
            color_pattern_label = _color_pattern_label(node.get("colorPatternMetafield"))
            for vedge in node["variants"]["edges"]:
                v = vedge["node"]
                sku = (v.get("sku") or "").strip()
                if not sku:
                    continue
                barcode = (v.get("barcode") or "").strip()
                qty = v.get("inventoryQuantity") or 0
                price_amount = str(v.get("price") or "")
                # color: the product-level color-pattern metaobject label
                # if set, else a real "Color"/"Colour" variant option ("" --
                # not a title-parsed guess -- when neither exists, e.g.
                # several Casilin and Boomba Bedding Set products have Size
                # only, no Color).
                # size: a real "Size" variant option (matches "Choose your
                # size" etc. too), with any leading quantity/noun phrase
                # stripped (e.g. "1 pillowcase 40x80" -> "40x80").
                selected_options = {
                    (opt.get("name") or "").strip().lower(): (opt.get("value") or "").strip()
                    for opt in (v.get("selectedOptions") or [])
                }
                color = color_pattern_label or _option_value_containing(selected_options, "colo")
                size = strip_size_quantity_prefix(_option_value_containing(selected_options, "size"))
                gender = gender_for(product_type, (v.get("genderMetafield") or {}).get("value"))
                age_group = age_group_for(product_type, (v.get("ageGroupMetafield") or {}).get("value"))
                rows.append(ProductRow(
                    handle=handle,
                    id=sku,
                    title=title,
                    description=description,
                    link=f"https://{STORE_DOMAIN_PUBLIC}{link_prefix}/products/{quote(link_handle)}?variant_sku={quote(sku)}",
                    image_link=image,
                    additional_image_link=",".join(additional_images),
                    price_amount=price_amount,
                    price=f"{price_amount} EUR" if price_amount else "",
                    availability="in_stock" if qty > 0 else "out_of_stock",
                    vendor=vendor,
                    brand=vendor,
                    condition="new",
                    gtin=usable_gtin(barcode),
                    mpn=sku,
                    item_group_id=handle,
                    color=color,
                    size=size,
                    material=material,
                    shipping_label=shipping_label_for(vendor, product_type),
                    translation_missing=translation_missing,
                    gender=gender,
                    age_group=age_group,
                ))
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return rows


# ---------------------------------------------------------------------------
# Pipeline: validate -> reject samples -> write feed + exclusions + report
# ---------------------------------------------------------------------------
FEED_COLUMNS = [
    "id", "title", "description", "link", "image_link", "additional_image_link",
    "availability", "price", "brand", "condition", "gtin", "mpn", "item_group_id",
    "color", "size", "material", "shipping_label", "gender", "age_group",
]


def run_pipeline(rows, out_basename, run_label, market="de"):
    accepted, excluded, sample_rejected, empty_description, missing_translation = [], [], [], [], []

    unknown_vendors = {}  # vendor -> offer count, flagged in the report
    for raw_row in rows:
        # Sample-data guard runs on the RAW row so a vendor literally named
        # "Google" is still caught before the mapping rewrites brand.
        sample_reason = is_known_sample_value(raw_row)
        if sample_reason:
            sample_rejected.append((raw_row, sample_reason))
            continue
        row, is_unknown = apply_brand_mapping(raw_row)
        if is_unknown:
            label = row["vendor"] or "(empty vendor)"
            unknown_vendors[label] = unknown_vendors.get(label, 0) + 1

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
    if unknown_vendors:
        report_lines.append(f"## Unknown vendors ({len(unknown_vendors)}) -- add to brands.yaml")
        report_lines.append(
            f"Not listed in brands.yaml, so brand defaulted to \"{load_brand_config()['default']}\" "
            "with the title unchanged. Decide consumer brand vs white-label. "
            "An empty vendor gets no brand and the offer is SKIPPED (see Validation exclusions):"
        )
        for vendor, count in sorted(unknown_vendors.items()):
            report_lines.append(f"- `{vendor}`: {count} offer(s)")
        report_lines.append("")
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
        "unknown_vendors": unknown_vendors,
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
    if stats["unknown_vendors"]:
        print(f"WARNING: unknown vendors (add to brands.yaml; non-empty ones defaulted to '{load_brand_config()['default']}', empty-vendor offers skipped): {stats['unknown_vendors']}")
    print(f"Feed written to {stats['feed_csv_path']} and {stats['feed_txt_path']}")
    print(f"Exclusions logged to {stats['exclusions_path']}")
    print(f"Report written to {stats['report_path']}")


if __name__ == "__main__":
    main()
