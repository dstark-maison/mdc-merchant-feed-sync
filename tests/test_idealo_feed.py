"""
Tests for idealo_feed.py.

Same style as tests/test_build_feed.py (plain pytest, tmp_path fixtures).
Covers idealo's own logic only -- shipping-tier boundaries, the imageUrls
semicolon-join, categoryPath mapping (both CSV and mocked shopify-api
sources), and one end-to-end run against the shared fixture CSV. Does NOT
re-test build_feed's own loaders/validators (already covered by
test_build_feed.py) beyond confirming idealo_feed calls them correctly.

Run with: pytest tests/ -v
"""
import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402
import idealo_feed  # noqa: E402

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_products_export.csv"


# ---------------------------------------------------------------------------
# shipping_cost_for_price -- tier boundaries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("price,expected", [
    ("0.01", "10.00"),
    ("700.00", "10.00"),        # top of tier 1 (inclusive)
    ("700.01", "120.00"),       # just over tier 1 -> tier 2
    ("1500.00", "120.00"),      # top of tier 2 (inclusive)
    ("1500.01", "300.00"),      # just over tier 2 -> top tier
    ("5000.00", "300.00"),
])
def test_shipping_cost_for_price_tier_boundaries(price, expected):
    assert idealo_feed.shipping_cost_for_price(price) == expected


def test_shipping_cost_for_price_unparseable_falls_back_to_top_tier():
    assert idealo_feed.shipping_cost_for_price("not-a-number") == idealo_feed.SHIPPING_TOP_TIER


def test_shipping_cost_for_price_none_falls_back_to_top_tier():
    assert idealo_feed.shipping_cost_for_price(None) == idealo_feed.SHIPPING_TOP_TIER


def test_shipping_cost_for_price_blank_falls_back_to_top_tier():
    assert idealo_feed.shipping_cost_for_price("") == idealo_feed.SHIPPING_TOP_TIER


# ---------------------------------------------------------------------------
# merge_image_urls -- semicolon join
# ---------------------------------------------------------------------------
def test_merge_image_urls_joins_image_link_and_additional_with_semicolons():
    row = build_feed.ProductRow(image_link="https://x/1.jpg", additional_image_link="https://x/2.jpg,https://x/3.jpg")
    assert idealo_feed.merge_image_urls(row) == "https://x/1.jpg;https://x/2.jpg;https://x/3.jpg"


def test_merge_image_urls_image_link_only_no_trailing_separator():
    row = build_feed.ProductRow(image_link="https://x/1.jpg", additional_image_link="")
    assert idealo_feed.merge_image_urls(row) == "https://x/1.jpg"


def test_merge_image_urls_blank_when_no_images():
    row = build_feed.ProductRow(image_link="", additional_image_link="")
    assert idealo_feed.merge_image_urls(row) == ""


def test_merge_image_urls_does_not_mutate_gmc_comma_joined_column():
    # The row's own additional_image_link (GMC's format) must stay untouched.
    row = build_feed.ProductRow(image_link="https://x/1.jpg", additional_image_link="https://x/2.jpg,https://x/3.jpg")
    idealo_feed.merge_image_urls(row)
    assert row["additional_image_link"] == "https://x/2.jpg,https://x/3.jpg"


# ---------------------------------------------------------------------------
# categoryPath -- CSV source
# ---------------------------------------------------------------------------
def test_load_category_paths_from_csv_reads_type_column():
    mapping = idealo_feed.load_category_paths_from_csv(str(FIXTURE_CSV))
    assert mapping["bamboo-fitted-sheet-mattress-topper-sky-blue"] == "Bedding"
    assert mapping["mens-pique-polo-shirt-SAMPLE"] == "Apparel"


def test_load_category_paths_from_csv_omits_handles_with_blank_type(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "Handle,Title,Body (HTML),Vendor,Type,Tags,Published,Variant SKU,Variant Price,"
        "Variant Inventory Qty,Variant Barcode,Image Src,Status\n"
        "no-type-product,No Type Product,<p>Desc</p>,Vendor,,tag,TRUE,SKU-1,10.00,5,"
        "4006381333931,https://x/1.jpg,active\n",
        encoding="utf-8",
    )
    mapping = idealo_feed.load_category_paths_from_csv(str(csv_path))
    assert "no-type-product" not in mapping


# ---------------------------------------------------------------------------
# categoryPath -- shopify-api source (mocked, own query)
# ---------------------------------------------------------------------------
@patch("idealo_feed.requests.post")
def test_load_category_paths_from_shopify_api_mocked(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    graphql_response = MagicMock()
    graphql_response.json.return_value = {
        "data": {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {"node": {"handle": "bamboo-sheet", "productType": "Bedding"}},
                    {"node": {"handle": "no-type-product", "productType": ""}},
                    {"node": {"handle": "no-type-field", "productType": None}},
                ],
            }
        }
    }
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    mapping = idealo_feed.load_category_paths_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert mapping == {"bamboo-sheet": "Bedding"}
    assert "no-type-product" not in mapping
    assert "no-type-field" not in mapping


@patch("idealo_feed.requests.post")
def test_load_category_paths_from_shopify_api_paginates(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    page1 = MagicMock()
    page1.json.return_value = {
        "data": {"products": {"pageInfo": {"hasNextPage": True, "endCursor": "CURSOR1"},
                               "edges": [{"node": {"handle": "a", "productType": "Bedding"}}]}}
    }
    page1.raise_for_status.return_value = None
    page2 = MagicMock()
    page2.json.return_value = {
        "data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                               "edges": [{"node": {"handle": "b", "productType": "Apparel"}}]}}
    }
    page2.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, page1, page2]

    mapping = idealo_feed.load_category_paths_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert mapping == {"a": "Bedding", "b": "Apparel"}
    assert mock_post.call_count == 3


# ---------------------------------------------------------------------------
# run_idealo_pipeline -- end-to-end against the shared fixture CSV
# ---------------------------------------------------------------------------
def test_run_idealo_pipeline_end_to_end_against_fixture(tmp_path):
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    category_paths = idealo_feed.load_category_paths_from_csv(str(FIXTURE_CSV))

    orig_data_dir, orig_reports_dir = idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR
    idealo_feed.DATA_DIR = tmp_path / "data"
    idealo_feed.REPORTS_DIR = tmp_path / "reports"
    idealo_feed.DATA_DIR.mkdir()
    idealo_feed.REPORTS_DIR.mkdir()
    try:
        stats = idealo_feed.run_idealo_pipeline(rows, category_paths, "unit_test_idealo_feed", "unit test run")

        assert stats["rows_read"] == 11
        assert stats["accepted"] == 1  # only the bamboo fitted sheet is fully valid
        assert stats["sample_rejected"] == 1  # the Google sample row
        assert stats["excluded"] == 9  # everything else (blank brand/description/price rows)

        assert stats["feed_path"].exists()
        assert stats["exclusions_path"].exists()
        assert stats["report_path"].exists()
        assert stats["report_path"].name.endswith("_idealo.md")

        lines = stats["feed_path"].read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        assert header == idealo_feed.IDEALO_COLUMNS
        assert len(lines) == 2  # header + 1 accepted row

        body = stats["feed_path"].read_text(encoding="utf-8")
        assert "8720828225332" in body  # the accepted row's sku
        assert "Bedding" in body  # its categoryPath
        assert "bamboo-fitted-sheet-sky-blue-2.jpg;bamboo-fitted-sheet-sky-blue-3.jpg" not in body  # sanity: not comma form
        assert ";" in body  # imageUrls semicolon-joined
        assert "10.00" in body  # deliveryCosts_dpd tier for a 144.00 EUR item
        assert "4-7 working days" in body
        assert "0.00" in body  # paymentCosts_paypal / paymentCosts_credit_card

        exclusions_body = stats["exclusions_path"].read_text(encoding="utf-8")
        assert "1111111111" in exclusions_body  # sample row logged
        assert "sample_data" in exclusions_body
    finally:
        idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir


def test_run_idealo_pipeline_never_publishes_sample_rows(tmp_path):
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    category_paths = {}

    orig_data_dir, orig_reports_dir = idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR
    idealo_feed.DATA_DIR = tmp_path / "data"
    idealo_feed.REPORTS_DIR = tmp_path / "reports"
    idealo_feed.DATA_DIR.mkdir()
    idealo_feed.REPORTS_DIR.mkdir()
    try:
        stats = idealo_feed.run_idealo_pipeline(rows, category_paths, "unit_test_idealo_feed_2", "unit test run")
        body = stats["feed_path"].read_text(encoding="utf-8")
        assert "Mens Pique Polo Shirt" not in body
        assert "Google" not in body
    finally:
        idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir


def test_run_idealo_pipeline_categorypath_blank_when_no_mapping_entry(tmp_path):
    row = build_feed.ProductRow(
        handle="no-mapping", id="SKU-1", title="Title", description="Desc", link="https://x",
        image_link="https://x.jpg", availability="in_stock", price="10.00 EUR", price_amount="10.00",
        brand="Brand", condition="new", gtin="4006381333931", mpn="SKU-1",
    )
    orig_data_dir, orig_reports_dir = idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR
    idealo_feed.DATA_DIR = tmp_path / "data"
    idealo_feed.REPORTS_DIR = tmp_path / "reports"
    idealo_feed.DATA_DIR.mkdir()
    idealo_feed.REPORTS_DIR.mkdir()
    try:
        stats = idealo_feed.run_idealo_pipeline([row], {}, "unit_test_idealo_feed_3", "unit test run")
        with open(stats["feed_path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            written = next(reader)
        assert written["categoryPath"] == ""
    finally:
        idealo_feed.DATA_DIR, idealo_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir
