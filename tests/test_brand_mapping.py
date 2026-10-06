"""
Tests for the GMC brand mapping (brands.yaml): vendor -> brand attribute and
"{Brand} {title}" prefixing. Covers the mapping itself, brand-in-title dedupe,
the 150-char cap, multilingual titles, and that the CSV and GraphQL adapters
(every market) produce identical results through run_pipeline().

Run with: pytest tests/ -v
"""
import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402

CONSUMER_VENDORS = ["Coco & Cici", "Boomba Bamboo", "MoST Blankets", "VIVARAISE"]
WHITE_LABEL_VENDORS = ["Ángel Cerdá S.L.", "Orderchamp", "Maison de Cocon"]
PASSTHROUGH_VENDORS = ["SalesFever"]  # held: brand stays the raw vendor, title untouched


# ---------------------------------------------------------------------------
# brands.yaml loading / validation
# ---------------------------------------------------------------------------
def test_shipped_brands_yaml_matches_the_agreed_mapping():
    cfg = build_feed.load_brand_config()
    assert cfg["default"] == "Maison de Cocon"
    assert cfg["consumer"] == {v: v for v in CONSUMER_VENDORS}
    assert set(WHITE_LABEL_VENDORS) <= cfg["white_label"]
    assert cfg["passthrough"] == set(PASSTHROUGH_VENDORS)


def test_every_shipping_label_vendor_is_mapped_in_brands_yaml():
    """A vendor that has a shipping label but no brand mapping would be
    flagged 'unknown' on every build -- keep the two lists in step."""
    cfg = build_feed.load_brand_config()
    known = set(cfg["consumer"]) | cfg["white_label"] | cfg["passthrough"]
    assert set(build_feed.VENDOR_SHIPPING_LABELS) <= known
    assert "SalesFever" in known


def test_config_requires_default_brand(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("consumer_brands: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="default_brand"):
        build_feed.load_brand_config.__wrapped__(p)


def test_config_rejects_vendor_in_both_sections(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text('default_brand: X\nconsumer_brands:\n  "A": "A"\nwhite_label:\n  - "A"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="more than one section"):
        build_feed.load_brand_config.__wrapped__(p)


# ---------------------------------------------------------------------------
# brand_for_vendor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("vendor", CONSUMER_VENDORS)
def test_consumer_vendor_maps_to_itself(vendor):
    assert build_feed.brand_for_vendor(vendor) == (vendor, True, False)


@pytest.mark.parametrize("vendor", WHITE_LABEL_VENDORS)
def test_white_label_vendor_maps_to_default_not_flagged(vendor):
    assert build_feed.brand_for_vendor(vendor) == ("Maison de Cocon", False, False)


@pytest.mark.parametrize("vendor", PASSTHROUGH_VENDORS)
def test_passthrough_vendor_keeps_its_own_brand_not_flagged(vendor):
    assert build_feed.brand_for_vendor(vendor) == (vendor, False, False)


@pytest.mark.parametrize("vendor", ["", None, "   "])
def test_empty_vendor_gets_no_brand_and_is_flagged(vendor):
    """Old behaviour: no brand -> validate_row skips the offer."""
    assert build_feed.brand_for_vendor(vendor) == ("", False, True)


@pytest.mark.parametrize("vendor", ["RIBECO", "Robinil", "coco & cici"])
def test_unknown_vendor_defaults_and_is_flagged(vendor):
    """Matching is exact: a case variant is unknown, not silently a brand."""
    assert build_feed.brand_for_vendor(vendor) == ("Maison de Cocon", False, True)


def test_vendor_whitespace_is_trimmed():
    assert build_feed.brand_for_vendor("  Coco & Cici ") == ("Coco & Cici", True, False)


# ---------------------------------------------------------------------------
# title_with_brand: prefix + dedupe
# ---------------------------------------------------------------------------
def test_prefixes_brand():
    assert build_feed.title_with_brand("Tencel Four Seasons Duvet", "Coco & Cici") == "Coco & Cici Tencel Four Seasons Duvet"


@pytest.mark.parametrize("title", [
    "Coco & Cici Tencel Duvet",
    "coco & cici tencel duvet",
    "COCO&CICI Tencel Duvet",
    "Tencel Duvet by Coco & Cici",
    "Coco-Cici Tencel Duvet",
])
def test_does_not_duplicate_brand_already_in_title(title):
    assert build_feed.title_with_brand(title, "Coco & Cici") == title


def test_dedupe_for_single_word_brand_case_insensitive():
    assert build_feed.title_with_brand("Vivaraise Kissenbezug", "VIVARAISE") == "Vivaraise Kissenbezug"


def test_empty_title_stays_empty():
    assert build_feed.title_with_brand("", "Coco & Cici") == ""
    assert build_feed.title_with_brand(None, "Coco & Cici") == ""


# ---------------------------------------------------------------------------
# title_with_brand: 150-char cap
# ---------------------------------------------------------------------------
def test_exactly_150_chars_is_untouched():
    title = "A" * (150 - len("MoST Blankets "))
    out = build_feed.title_with_brand(title, "MoST Blankets")
    assert len(out) == 150 and out == f"MoST Blankets {title}"


def test_over_150_drops_trailing_segments_first():
    title = "Tencel Duvet - " + "x" * 60 + " - " + "y" * 70
    out = build_feed.title_with_brand(title, "Coco & Cici")
    assert out == "Coco & Cici Tencel Duvet - " + "x" * 60
    assert len(out) <= 150


def test_over_150_without_separators_cuts_at_word_boundary():
    words = " ".join(["Wort"] * 40)  # 199 chars, no separators
    out = build_feed.title_with_brand(words, "Boomba Bamboo")
    assert len(out) <= 150
    assert out.startswith("Boomba Bamboo Wort")
    assert out.endswith("Wort")  # no half word, no trailing space


def test_over_150_never_cuts_brand_or_leading_product_term():
    title = "Bettbezug " + "z" * 200  # one giant unbreakable token after the product term
    out = build_feed.title_with_brand(title, "VIVARAISE")
    assert out.startswith("VIVARAISE Bettbezug")
    assert len(out) <= 150


def test_over_150_hard_cut_when_no_space_anywhere_after_brand():
    out = build_feed.title_with_brand("q" * 200, "Boomba Bamboo")
    assert out.startswith("Boomba Bamboo")
    assert len(out) <= 150


def test_title_already_over_150_with_brand_is_not_modified():
    title = "Coco & Cici " + "word " * 40
    assert build_feed.title_with_brand(title.strip(), "Coco & Cici") == title.strip()


# ---------------------------------------------------------------------------
# multilingual titles: brand untranslated in every language
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,expected", [
    ("Tencel Ganzjahres-Bettdecke 4 Jahreszeiten", "Coco & Cici Tencel Ganzjahres-Bettdecke 4 Jahreszeiten"),
    ("Couette Tencel quatre saisons", "Coco & Cici Couette Tencel quatre saisons"),
    ("Tencel dekbed vier seizoenen", "Coco & Cici Tencel dekbed vier seizoenen"),
    ("Tencel Four Seasons Duvet", "Coco & Cici Tencel Four Seasons Duvet"),
])
def test_brand_untranslated_in_every_language(title, expected):
    assert build_feed.title_with_brand(title, "Coco & Cici") == expected


def test_accents_and_umlauts_survive_truncation():
    title = "Überzug für Kopfkissen " + "größe ä ö ü é " * 20
    out = build_feed.title_with_brand(title, "VIVARAISE")
    assert out.startswith("VIVARAISE Überzug für Kopfkissen")
    assert len(out) <= 150


# ---------------------------------------------------------------------------
# apply_brand_mapping
# ---------------------------------------------------------------------------
def _row(vendor, title="Tencel Duvet"):
    return build_feed.ProductRow(id="X-1", title=title, vendor=vendor, brand=vendor)


def test_consumer_brand_row_gets_brand_and_prefixed_title():
    new, unknown = build_feed.apply_brand_mapping(_row("Coco & Cici"))
    assert (new["brand"], new["title"], unknown) == ("Coco & Cici", "Coco & Cici Tencel Duvet", False)
    assert new["vendor"] == "Coco & Cici"


@pytest.mark.parametrize("vendor", WHITE_LABEL_VENDORS)
def test_white_label_row_gets_default_brand_and_title_is_untouched(vendor):
    new, unknown = build_feed.apply_brand_mapping(_row(vendor))
    assert (new["brand"], new["title"], unknown) == ("Maison de Cocon", "Tencel Duvet", False)
    assert vendor.lower() not in new["title"].lower() or vendor == "Maison de Cocon"


def test_unknown_vendor_row_is_flagged_default_brand_title_untouched():
    new, unknown = build_feed.apply_brand_mapping(_row("RIBECO"))
    assert (new["brand"], new["title"], unknown) == ("Maison de Cocon", "Tencel Duvet", True)


@pytest.mark.parametrize("vendor", PASSTHROUGH_VENDORS)
def test_passthrough_row_keeps_brand_and_title(vendor):
    new, unknown = build_feed.apply_brand_mapping(_row(vendor))
    assert (new["brand"], new["title"], unknown) == (vendor, "Tencel Duvet", False)


def test_empty_vendor_row_stays_brandless_flagged_and_fails_validation():
    new, unknown = build_feed.apply_brand_mapping(_row(""))
    assert new["brand"] == "" and unknown is True
    new.update(price_amount="1", description="d", link="l", image_link="i", price="1 EUR",
               availability="in_stock", mpn="m", id="1")
    ok, reasons = build_feed.validate_row(new)
    assert ok is False and "missing required field 'brand'" in reasons


def test_brand_read_from_brand_when_vendor_key_absent():
    new, _ = build_feed.apply_brand_mapping(build_feed.ProductRow(id="1", title="T", brand="VIVARAISE"))
    assert new["brand"] == "VIVARAISE" and new["title"] == "VIVARAISE T"


# ---------------------------------------------------------------------------
# adapters + pipeline parity: CSV and GraphQL (all markets) end up identical
# ---------------------------------------------------------------------------
TITLES = {
    "de": "Tencel Ganzjahres-Bettdecke",
    "be-fr": "Couette Tencel quatre saisons",
    "be-nl": "Tencel dekbed vier seizoenen",
    "en": "Tencel Four Seasons Duvet",
}
CSV_HEADER = ["Handle", "Title", "Body (HTML)", "Vendor", "Type", "Status", "Variant SKU",
              "Variant Price", "Variant Barcode", "Variant Inventory Qty", "Image Src"]


def _write_csv(path, vendor, title):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerow(["duvet", title, "<p>Beschreibung</p>", vendor, "Duvets", "active", "SKU-1",
                    "99.00", "4006381333931", "5", "https://x/1.jpg"])


def _graphql_response(vendor, market, title):
    node = {
        "handle": "duvet", "title": TITLES["en"], "descriptionHtml": "<p>Beschreibung</p>",
        "vendor": vendor, "productType": "Duvets", "status": "ACTIVE",
        "featuredImage": {"url": "https://x/1.jpg"}, "images": {"nodes": [{"url": "https://x/1.jpg"}]},
        "translations": [{"key": "title", "value": title}, {"key": "body_html", "value": "<p>Beschreibung</p>"}],
        "variants": {"edges": [{"node": {"sku": "SKU-1", "price": "99.00", "barcode": "4006381333931", "inventoryQuantity": 5}}]},
    }
    if market == "en":
        node["title"] = title
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                       "edges": [{"node": node}]}}}
    return resp


def _run(tmp_path, monkeypatch, rows, name):
    monkeypatch.setattr(build_feed, "DATA_DIR", tmp_path / name / "data")
    monkeypatch.setattr(build_feed, "REPORTS_DIR", tmp_path / name / "reports")
    build_feed.DATA_DIR.mkdir(parents=True)
    build_feed.REPORTS_DIR.mkdir(parents=True)
    stats = build_feed.run_pipeline(rows, "f", "test")
    with open(stats["feed_csv_path"], newline="", encoding="utf-8") as f:
        return stats, list(csv.DictReader(f))


@pytest.mark.parametrize("market", sorted(TITLES))
@pytest.mark.parametrize("vendor,expect_brand,prefixed", [
    ("Coco & Cici", "Coco & Cici", True),
    ("Boomba Bamboo", "Boomba Bamboo", True),
    ("MoST Blankets", "MoST Blankets", True),
    ("VIVARAISE", "VIVARAISE", True),
    ("SalesFever", "SalesFever", False),
    ("Maison de Cocon", "Maison de Cocon", False),
])
@patch("build_feed.requests.post")
def test_csv_and_graphql_adapters_agree_in_every_market(mock_post, market, vendor, expect_brand, prefixed, tmp_path, monkeypatch):
    title = TITLES[market]
    expected_title = f"{expect_brand} {title}" if prefixed else title

    build_feed._cached_token = None
    token = MagicMock()
    token.json.return_value = {"access_token": "t"}
    token.raise_for_status.return_value = None
    mock_post.side_effect = [token, _graphql_response(vendor, market, title)]
    api_rows = build_feed.load_products_from_shopify_api("s.myshopify.com", "i", "s", market=market)
    _, api_out = _run(tmp_path, monkeypatch, api_rows, "api")

    csv_path = tmp_path / "export.csv"
    _write_csv(csv_path, vendor, title)
    _, csv_out = _run(tmp_path, monkeypatch, build_feed.load_products_from_csv(str(csv_path)), "csv")

    assert len(api_out) == len(csv_out) == 1
    assert api_out[0]["title"] == csv_out[0]["title"] == expected_title
    assert api_out[0]["brand"] == csv_out[0]["brand"] == expect_brand
    assert len(api_out[0]["title"]) <= 150


def test_pipeline_item_count_unchanged_by_mapping(tmp_path, monkeypatch):
    rows = [_row(v) for v in CONSUMER_VENDORS + WHITE_LABEL_VENDORS + PASSTHROUGH_VENDORS + ["RIBECO", ""]]
    for i, r in enumerate(rows):
        r.update(id=f"S-{i}", description="d", link="https://www.maisondecocon.com/p", image_link="https://x/1.jpg",
                 price="1.00 EUR", price_amount="1.00", availability="in_stock", mpn=f"S-{i}", gtin="")
    stats, out = _run(tmp_path, monkeypatch, rows, "count")
    assert stats["accepted"] == len(rows) - 1 == len(out)  # the empty-vendor row is skipped
    assert stats["excluded"] == 1
    assert all(r["brand"] for r in out)  # no published row has an empty brand
    assert stats["unknown_vendors"] == {"RIBECO": 1, "(empty vendor)": 1}


def test_unknown_vendors_are_listed_in_the_report(tmp_path, monkeypatch):
    rows = [_row("RIBECO")]
    rows[0].update(id="S-1", description="d", link="https://www.maisondecocon.com/p", image_link="https://x/1.jpg",
                   price="1.00 EUR", price_amount="1.00", availability="in_stock", mpn="S-1", gtin="")
    stats, _ = _run(tmp_path, monkeypatch, rows, "report")
    text = stats["report_path"].read_text(encoding="utf-8")
    assert "Unknown vendors" in text and "`RIBECO`" in text


def test_sample_guard_still_sees_the_raw_vendor_as_brand(tmp_path, monkeypatch):
    """A vendor literally named 'Google' must still be hard-rejected: the
    sample check runs before the mapping can rewrite it to the default brand."""
    row = _row("Google")
    row.update(id="S-1", description="d", link="https://www.maisondecocon.com/p", image_link="https://x/1.jpg",
               price="1.00 EUR", price_amount="1.00", availability="in_stock", mpn="S-1", gtin="")
    stats, out = _run(tmp_path, monkeypatch, [row], "sample")
    assert stats["sample_rejected"] == 1 and out == []


def test_idealo_inputs_are_unaffected_by_the_mapping():
    """The adapters still emit brand=vendor + the raw title (idealo_feed.py keys
    its shipping/delivery tables on brand and publishes the raw title)."""
    rows = build_feed.load_products_from_csv(str(Path(__file__).parent / "fixtures" / "sample_products_export.csv"))
    boomba = next(r for r in rows if r["vendor"] == "Boomba Bamboo")
    assert boomba["brand"] == "Boomba Bamboo" and not boomba["title"].startswith("Boomba Bamboo")
