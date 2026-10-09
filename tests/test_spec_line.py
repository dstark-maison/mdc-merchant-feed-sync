"""Tests for the labelled spec line / color-material-pattern enrichment in build_feed.py."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402

CFG = {
    "Boomba Bamboo": {"material": {l: f"MAT-{l}" for l in "en de fr nl".split()}, "thread_count": "400",
                      "pattern": {"en": "Solid", "de": "Uni", "fr": "Uni", "nl": "Effen"}},
    "VIVARAISE": {"material": {l: f"VMAT-{l}" for l in "en de fr nl".split()}, "thread_count": None,
                  "pattern": {"en": "", "de": "", "fr": "", "nl": ""}},
}


def row(vendor="Boomba Bamboo", **kw):
    return build_feed.ProductRow(vendor=vendor, description="Base text.", color="Coco White", option_color="Coco white", **kw)


@pytest.mark.parametrize("loc,expected", [
    ("en", "Colour: Coco White · Material: MAT-en · Thread count: 400 · Pattern: Solid"),
    ("de", "Farbe: Coco White · Material: MAT-de · Fadenzahl: 400 · Muster: Uni"),
    ("fr", "Couleur : Coco White · Matière : MAT-fr · Nombre de fils : 400 · Motif : Uni"),
    ("nl", "Kleur: Coco White · Materiaal: MAT-nl · Draaddichtheid: 400 · Patroon: Effen"),
])
def test_boomba_spec_line_per_language(loc, expected):
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, CFG, clear=True):
        new, configured = build_feed.apply_spec_enrichment(row(), loc)
    assert configured
    assert new["description"] == f"Base text.\n{expected}"
    assert (new["color"], new["material"], new["pattern"]) == ("Coco White", f"MAT-{loc}", CFG["Boomba Bamboo"]["pattern"][loc])


def test_vivaraise_omits_thread_count_and_empty_pattern():
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, CFG, clear=True):
        new, _ = build_feed.apply_spec_enrichment(row("VIVARAISE"), "en")
    assert new["description"] == "Base text.\nColour: Coco White · Material: VMAT-en"


def test_missing_colour_segment_skipped():
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, CFG, clear=True):
        new, _ = build_feed.apply_spec_enrichment(build_feed.ProductRow(vendor="VIVARAISE", description="D"), "de")
    assert new["description"] == "D\nMaterial: VMAT-de"


def test_empty_material_fails_loudly():
    cfg = {"Boomba Bamboo": {**CFG["Boomba Bamboo"], "material": {"en": "", "de": "x", "fr": "x", "nl": "x"}}}
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, cfg, clear=True):
        with pytest.raises(build_feed.SpecConfigError):
            build_feed.apply_spec_enrichment(row(), "en")


def test_other_vendor_untouched():
    r = row("MoST Blankets")
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, CFG, clear=True):
        new, configured = build_feed.apply_spec_enrichment(r, "en")
    assert new is r and not configured


def test_5000_char_limit_truncates_description_not_spec():
    spec = "Colour: X · Material: Y"
    out = build_feed.append_spec_line("word " * 2000, spec)
    assert len(out) <= build_feed.MAX_DESCRIPTION_LEN
    assert out.endswith("\n" + spec) and "...\n" in out


def test_idempotent():
    spec = "Colour: X"
    once = build_feed.append_spec_line("D", spec)
    assert build_feed.append_spec_line(once, spec) == once


PENDING = {
    "Boomba Bamboo": {"material_pending": True, "material": {}, "thread_count": "400",
                      "pattern": {"en": "Solid", "de": "Uni", "fr": "Uni", "nl": "Effen"},
                      "pattern_handle_prefixes": ("bamboo-fitted-sheet",)},
}


def test_material_pending_drops_material_and_ignores_fabric_metafield():
    r = row(handle="bamboo-fitted-sheet-coco-white-400tc", material="100% Bamboo (Tanboocel)")
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, PENDING, clear=True):
        new, _ = build_feed.apply_spec_enrichment(r, "en")
    assert new["material"] == ""
    assert new["description"] == "Base text.\nColour: Coco White · Thread count: 400 · Pattern: Solid"
    assert "amboo" not in new["description"].replace("Base", "")


def test_material_pending_flag_is_per_vendor_others_still_raise():
    cfg = {**PENDING, "VIVARAISE": {"material": {"en": ""}, "thread_count": None, "pattern": {}}}
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, cfg, clear=True):
        with pytest.raises(build_feed.SpecConfigError):
            build_feed.apply_spec_enrichment(row("VIVARAISE"), "en")


@pytest.mark.parametrize("handle,has_pattern", [
    ("bamboo-fitted-sheet-coco-white-400tc", True),
    ("bamboo-fitted-sheet-for-mattress-topper-sage-green-400tc", True),
    ("1-person-bamboo-bedding-set-sky-blue-400tc", False),
    ("2-person-bamboo-bedding-set-sky-blue-400tc", False),
    ("bamboo-duvet-cover-coco-white-400tc", False),
    ("bamboo-pillowcases-coco-white-400tc", False),
])
def test_pattern_only_for_fitted_sheets(handle, has_pattern):
    with patch.dict(build_feed.VENDOR_SPEC_CONFIG, PENDING, clear=True):
        new, _ = build_feed.apply_spec_enrichment(row(handle=handle), "de")
    assert (new["pattern"] == "Uni") is has_pattern
    assert ("Muster" in new["description"]) is has_pattern
    if not has_pattern:
        assert new["pattern"] == ""


def test_colour_title_case():
    assert build_feed.title_case_colour("Coco white") == "Coco White"
    assert build_feed.title_case_colour("soft taupe") == "Soft Taupe"
    assert build_feed.title_case_colour("Coffee Brown") == "Coffee Brown"
    assert build_feed.title_case_colour("") == ""


@pytest.mark.real_spec_config
def test_shipped_config_boomba():
    c = build_feed.VENDOR_SPEC_CONFIG["Boomba Bamboo"]
    assert c["material_pending"] is True and c["thread_count"] == "400"
    for loc in ("en", "de", "fr", "nl"):  # must build for every locale without raising
        new, _ = build_feed.apply_spec_enrichment(row(handle="bamboo-fitted-sheet-x"), loc)
        assert new["material"] == "" and new["color"] == "Coco White"


@pytest.mark.real_spec_config
@pytest.mark.parametrize("loc", ["en", "de", "fr", "nl"])
def test_vivaraise_offers_unchanged_with_shipped_config(loc):
    """VIVARAISE: no spec line, no material, no pattern, no colour change -- identical to the pre-feature feed."""
    c = build_feed.VENDOR_SPEC_CONFIG["VIVARAISE"]
    assert c["material_pending"] is True and not c["thread_count"]
    r = build_feed.ProductRow(vendor="VIVARAISE", brand="VIVARAISE", description="Fara cushion.", color="rouge",
                              option_color="rouge", material="Cover: 100% cotton; filling: polyester")
    new, configured = build_feed.apply_spec_enrichment(r, loc)
    assert new is r and not configured
    assert new == r and "pattern" not in new


@pytest.mark.real_spec_config
def test_vivaraise_pipeline_output_unchanged(tmp_path, monkeypatch):
    import csv
    monkeypatch.setattr(build_feed, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_feed, "REPORTS_DIR", tmp_path)
    r = build_feed.ProductRow(
        handle="cushion-fara-x", id="V1", title="Kissen Fara", description="Eine Fara-Decke.", link="https://x/p",
        image_link="https://x/i.jpg", price_amount="10.00", price="10.00 EUR", availability="in_stock",
        vendor="VIVARAISE", brand="VIVARAISE", condition="new", gtin="", mpn="V1", item_group_id="cushion-fara-x",
        color="", option_color="", size="", material="Polyester", shipping_label="std_15", gender="", age_group="")
    build_feed.run_pipeline([r], "t", "test", market="de")
    out = list(csv.DictReader(open(tmp_path / "t.csv", newline="", encoding="utf-8")))[0]
    assert out["description"] == "Eine Fara-Decke."
    assert out["material"] == "Polyester" and out["color"] == "" and out["pattern"] == ""


def test_product_line_never_reaches_the_feed():
    """2026-10-09: the product-line display is disabled on the live theme; no feed field may carry its value."""
    import json
    from unittest.mock import MagicMock
    assert "product_line" not in build_feed.PRODUCTS_QUERY and "productLine" not in build_feed.PRODUCTS_QUERY
    node = {"handle": "cushion-fera-x", "title": "Kissen", "descriptionHtml": "<p>Weich.</p>", "vendor": "VIVARAISE",
            "productType": "Cushions", "featuredImage": {"url": "https://cdn/x.jpg"},
            "productLineMetafield": {"value": "gid://shopify/Metaobject/1", "reference": {"displayName": "Fera"}},
            "metafields": [{"key": "product_line", "value": "Fera"}],
            "variants": {"edges": [{"node": {"legacyResourceId": 1, "sku": "S1", "price": "10.00", "barcode": "",
                                              "inventoryQuantity": 3,
                                              "selectedOptions": [{"name": "Color", "value": "Rouge"}]}}]}}
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    tok = MagicMock(); tok.raise_for_status.return_value = None; tok.json.return_value = {"access_token": "t"}
    build_feed._cached_token = None
    with patch("build_feed.requests.post", side_effect=[tok, resp]):
        rows = build_feed.load_products_from_shopify_api("s.myshopify.com", "i", "s", market="en")
    out, _ = build_feed.apply_brand_mapping(rows[0])
    out, _ = build_feed.apply_spec_enrichment(out, "en")
    assert "Fera" not in json.dumps({k: v for k, v in out.items() if k in build_feed.FEED_COLUMNS})
    assert "Fera" not in json.dumps(dict(out))
