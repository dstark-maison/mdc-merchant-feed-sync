"""
Tests for (1) the brand/title overlap merge and (2) the brands.yaml mapping as
applied to the idealo feed (shared function, same rules as the GMC feed).
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402
import idealo_feed  # noqa: E402


# ---------------------------------------------------------------------------
# brand/title overlap merge: title starts with the brand's trailing word(s)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,brand,expected", [
    ("Bamboo Fitted Sheet Soft Taupe", "Boomba Bamboo", "Boomba Bamboo Fitted Sheet Soft Taupe"),
    ("bamboo duvet cover", "Boomba Bamboo", "Boomba Bamboo duvet cover"),
    ("BAMBOO™ Duvet Cover", "Boomba Bamboo", "Boomba Bamboo Duvet Cover"),
    ("Bamboo® Duvet Cover", "Boomba Bamboo", "Boomba Bamboo Duvet Cover"),
    ("Blankets Throw Venezia", "MoST Blankets", "MoST Blankets Throw Venezia"),
    ("Cici Duvet", "Coco & Cici", "Coco & Cici Duvet"),
])
def test_overlap_with_brand_tail_is_merged(title, brand, expected):
    assert build_feed.title_with_brand(title, brand) == expected


@pytest.mark.parametrize("title", [
    "Bambus-Bettwäscheset für 1 Person, 400TC",  # DE: not the English word
    "Bambus Spannbetttuch",
    "Parure de lit en bambou",                            # FR
    "Bamboe dekbedovertrek, hemelsblauw",                 # NL
    "Bamboo-Spannbetttuch",                               # hyphenated compound is one word
    "Bamboozle Sheet",                                    # whole words only
])
def test_overlap_never_merges_other_languages_or_partial_words(title):
    assert build_feed.title_with_brand(title, "Boomba Bamboo") == f"Boomba Bamboo {title}"


def test_overlap_only_in_leading_position():
    assert build_feed.title_with_brand("Fitted Bamboo Sheet", "Boomba Bamboo") == "Boomba Bamboo Fitted Bamboo Sheet"


def test_title_consisting_only_of_the_overlap_is_kept():
    assert build_feed.title_with_brand("Bamboo", "Boomba Bamboo") == "Boomba Bamboo Bamboo"


def test_single_word_brand_has_no_overlap_to_merge():
    assert build_feed.title_with_brand("Kissen Fara", "VIVARAISE") == "VIVARAISE Kissen Fara"


def test_overlap_merge_then_length_cap():
    out = build_feed.title_with_brand("Bamboo " + "Wort " * 40, "Boomba Bamboo")
    assert out.startswith("Boomba Bamboo Wort") and len(out) <= 150


# ---------------------------------------------------------------------------
# idealo: same mapping, shared function
# ---------------------------------------------------------------------------
def _row(vendor, title, sku, availability="in_stock"):
    return build_feed.ProductRow(
        handle=f"h-{sku}", id=sku, title=title, vendor=vendor, brand=vendor, description="d",
        link="https://www.maisondecocon.com/products/x?variant_sku=" + sku, image_link="https://x/1.jpg",
        additional_image_link="", price_amount="9.99", price="9.99 EUR", availability=availability,
        condition="new", gtin="4006381333931", mpn=sku, item_group_id=f"h-{sku}", color="", size="",
        material="", shipping_label="x", gender="", age_group="",
    )


def _run(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(idealo_feed, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(idealo_feed, "REPORTS_DIR", tmp_path / "reports")
    idealo_feed.DATA_DIR.mkdir()
    idealo_feed.REPORTS_DIR.mkdir()
    stats = idealo_feed.run_idealo_pipeline(rows, {}, "idealo_t", "test")
    with open(stats["feed_path"], newline="", encoding="utf-8") as f:
        return stats, list(csv.DictReader(f))


def test_idealo_applies_brand_mapping_titles_and_overlap(tmp_path, monkeypatch):
    rows = [
        _row("Boomba Bamboo", "Bamboo Fitted Sheet Soft Taupe", "A"),
        _row("Coco & Cici", "Bettbezug aus Tencel™-Twill, Weiß", "B"),
        _row("MoST Blankets", "Woll-Decke «Venezia»", "C"),
        _row("VIVARAISE", "Kissen Fara Bronze", "D"),
        _row("SalesFever", "Polsterbett aus beigem Samt, 90×200", "E"),
        _row("", "Ohne Vendor", "H"),
    ]
    stats, out = _run(tmp_path, monkeypatch, rows)
    by = {r["sku"]: r for r in out}
    assert (by["A"]["brand"], by["A"]["title"]) == ("Boomba Bamboo", "Boomba Bamboo Fitted Sheet Soft Taupe")
    assert (by["B"]["brand"], by["B"]["title"]) == ("Coco & Cici", "Coco & Cici Bettbezug aus Tencel™-Twill, Weiß")
    assert (by["C"]["brand"], by["C"]["title"]) == ("MoST Blankets", "MoST Blankets Woll-Decke «Venezia»")
    assert (by["D"]["brand"], by["D"]["title"]) == ("VIVARAISE", "VIVARAISE Kissen Fara Bronze")
    # SalesFever: raw vendor as brand (GTIN owner), title untouched
    assert (by["E"]["brand"], by["E"]["title"]) == ("SalesFever", "Polsterbett aus beigem Samt, 90×200")
    # empty vendor: skipped + flagged
    assert "H" not in by and stats["excluded"] == 1 and stats["accepted"] == 5
    assert stats["unknown_vendors"] == {"(empty vendor)": 1}
    assert "Unknown vendors" in stats["report_path"].read_text(encoding="utf-8")


def test_idealo_shipping_and_delivery_still_keyed_on_vendor_not_mapped_brand(tmp_path, monkeypatch):
    stats, out = _run(tmp_path, monkeypatch, [
        _row("SalesFever", "Polsterbett", "E"),
        _row("VIVARAISE", "Kissen", "D"),
        _row("Coco & Cici", "Bettbezug", "B"),
    ])
    by = {r["sku"]: r for r in out}
    assert by["E"]["delivery"] == idealo_feed.VENDOR_DELIVERY_TEXT["SalesFever"]
    assert by["D"]["delivery"] == idealo_feed.VENDOR_DELIVERY_TEXT["VIVARAISE"]
    assert by["B"]["delivery"] == idealo_feed.DELIVERY_TEXT
    assert by["E"]["deliveryCosts_dpd"] == idealo_feed.IDEALO_SHIPPING_RATES_DE["sf_bulky"]
    assert by["D"]["deliveryCosts_dpd"] == idealo_feed.IDEALO_SHIPPING_RATES_DE["std_15"]


def test_idealo_item_count_unchanged_by_mapping(tmp_path, monkeypatch):
    rows = [_row(v, "Titel", f"S{i}") for i, v in enumerate(
        ["Coco & Cici", "Boomba Bamboo", "MoST Blankets", "VIVARAISE", "SalesFever"])]
    stats, out = _run(tmp_path, monkeypatch, rows)
    assert stats["accepted"] == len(rows) == len(out)


def test_idealo_and_gmc_share_one_mapping_function():
    assert idealo_feed.apply_brand_mapping is build_feed.apply_brand_mapping


def test_idealo_unknown_vendor_still_fails_loudly_on_missing_shipping_rate(tmp_path, monkeypatch):
    """Pre-existing guard, unchanged: a vendor with no idealo shipping rate
    must break the build rather than get a guessed rate."""
    with pytest.raises(ValueError, match="No idealo DE shipping rate"):
        _run(tmp_path, monkeypatch, [_row("RIBECO", "Neues Produkt", "G")])
