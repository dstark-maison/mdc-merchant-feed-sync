"""
Tests for build_feed.py.

Covers both input adapters:
  - load_products_from_csv() against a real fixture (tests/fixtures/sample_products_export.csv)
  - load_products_from_shopify_api() against a mocked GraphQL response, since
    no live Shopify API credentials exist in this environment by design (see
    README -- credential creation is a deliberate manual go-live step, not
    something this build session does on its own).

Run with: pytest tests/ -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_feed  # noqa: E402

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_products_export.csv"


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------
def test_strip_html_removes_tags_and_collapses_whitespace():
    raw = "<p>Hello   <strong>world</strong></p>\n<p>Second line.</p>"
    assert build_feed.strip_html(raw) == "Hello world Second line."


def test_strip_html_decodes_entities():
    assert build_feed.strip_html("<p>Tom &amp; Jerry&#39;s</p>") == "Tom & Jerry's"


def test_strip_html_empty_input():
    assert build_feed.strip_html("") == ""
    assert build_feed.strip_html(None) == ""


# ---------------------------------------------------------------------------
# gtin_checksum_valid
# ---------------------------------------------------------------------------
def test_gtin_checksum_valid_real_ean13():
    assert build_feed.gtin_checksum_valid("4006381333931") is True


def test_gtin_checksum_valid_rejects_all_zeros():
    # All-zero passes length but fails on being a real check-digit-correct
    # code is coincidental either way -- the sample-value list catches this
    # specific case explicitly; this test only covers the checksum math.
    assert build_feed.gtin_checksum_valid("000000000000") is True  # 12 zeros: checksum trivially 0==0
    # -- confirms WHY the sample-value list (not just checksum) is needed to catch this.


def test_gtin_checksum_valid_rejects_wrong_length():
    assert build_feed.gtin_checksum_valid("12345") is False


def test_gtin_checksum_valid_rejects_bad_check_digit():
    assert build_feed.gtin_checksum_valid("4006381333939") is False


# ---------------------------------------------------------------------------
# is_known_sample_value
# ---------------------------------------------------------------------------
def test_sample_value_catches_known_id():
    row = build_feed.ProductRow(id="1111111111", title="Anything", brand="X", link="", image_link="", gtin="")
    reason = build_feed.is_known_sample_value(row)
    assert reason is not None
    assert "1111111111" in reason


def test_sample_value_catches_known_title_case_insensitive():
    row = build_feed.ProductRow(id="x", title="MENS PIQUE POLO SHIRT", brand="X", link="", image_link="", gtin="")
    assert build_feed.is_known_sample_value(row) is not None


def test_sample_value_catches_google_brand():
    row = build_feed.ProductRow(id="x", title="Anything", brand="Google", link="", image_link="", gtin="")
    assert build_feed.is_known_sample_value(row) is not None


def test_sample_value_catches_example_domain_link():
    row = build_feed.ProductRow(id="x", title="Anything", brand="X", link="http://www.example.com/asp/sp.asp?id=1", image_link="", gtin="")
    assert build_feed.is_known_sample_value(row) is not None


def test_sample_value_catches_placeholder_gtin():
    row = build_feed.ProductRow(id="x", title="Anything", brand="X", link="", image_link="", gtin="0000000000000")
    assert build_feed.is_known_sample_value(row) is not None


def test_sample_value_catches_example_domain_additional_image_link():
    row = build_feed.ProductRow(id="x", title="Anything", brand="X", link="", image_link="https://cdn.shopify.com/x.jpg",
                                 additional_image_link="http://www.example.com/image2.jpg", gtin="")
    assert build_feed.is_known_sample_value(row) is not None


def test_sample_value_ignores_normal_row():
    row = build_feed.ProductRow(id="8720828225332", title="Bamboo Fitted Sheet", brand="Boomba Bamboo",
                                 link="https://www.maisondecocon.com/products/x", image_link="https://cdn.shopify.com/x.jpg",
                                 gtin="8720828225332")
    assert build_feed.is_known_sample_value(row) is None


# ---------------------------------------------------------------------------
# validate_row
# ---------------------------------------------------------------------------
def _complete_row(**overrides):
    row = build_feed.ProductRow(
        id="123", title="Title", description="Desc", link="https://x", image_link="https://x.jpg",
        availability="in_stock", price="10.00 EUR", price_amount="10.00", brand="Brand",
        condition="new", gtin="4006381333931", mpn="123",
    )
    row.update(overrides)
    return row


def test_validate_row_accepts_complete_row():
    is_valid, reasons = build_feed.validate_row(_complete_row())
    assert is_valid is True
    assert reasons == []


@pytest.mark.parametrize("field", ["id", "title", "description", "link", "image_link", "price", "availability"])
def test_validate_row_rejects_missing_required_field(field):
    row = _complete_row(**{field: ""})
    is_valid, reasons = build_feed.validate_row(row)
    assert is_valid is False
    assert any(field in r for r in reasons)


def test_validate_row_rejects_missing_brand():
    is_valid, reasons = build_feed.validate_row(_complete_row(brand=""))
    assert is_valid is False
    assert any("brand" in r for r in reasons)


def test_validate_row_rejects_no_identifier():
    is_valid, reasons = build_feed.validate_row(_complete_row(gtin="", mpn=""))
    assert is_valid is False
    assert any("identifier" in r for r in reasons)


def test_validate_row_rejects_bad_gtin_checksum():
    is_valid, reasons = build_feed.validate_row(_complete_row(gtin="1234567890123"))
    assert is_valid is False
    assert any("checksum" in r for r in reasons)


def test_validate_row_accepts_mpn_only_no_gtin():
    is_valid, reasons = build_feed.validate_row(_complete_row(gtin="", mpn="SKU-001"))
    assert is_valid is True


def test_validate_row_rejects_non_numeric_price():
    is_valid, reasons = build_feed.validate_row(_complete_row(price_amount="not-a-number"))
    assert is_valid is False
    assert any("numeric" in r for r in reasons)


def test_validate_row_rejects_zero_price():
    is_valid, reasons = build_feed.validate_row(_complete_row(price_amount="0"))
    assert is_valid is False


# ---------------------------------------------------------------------------
# load_products_from_csv (real fixture, end-to-end for the offline path)
# ---------------------------------------------------------------------------
def test_load_products_from_csv_reads_fixture():
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    assert len(rows) == 11
    ids = {r["id"] for r in rows}
    assert "8720828225332" in ids  # the one fully-complete real row


def test_load_products_from_csv_strips_html_in_description():
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    sheet = next(r for r in rows if r["id"] == "8720828225332")
    assert "<p>" not in sheet["description"]
    assert sheet["description"].startswith("A premium 100% bamboo fitted sheet")


def test_load_products_from_csv_skips_rows_without_sku():
    # every row in the fixture has a SKU; this asserts the skip logic exists
    # and doesn't crash on a handle carried across multiple option rows.
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    assert all(r["id"] for r in rows)


def test_load_products_from_csv_builds_additional_image_link_from_image_only_rows():
    # The fixture's bamboo-fitted-sheet handle has two image-only
    # continuation rows (blank Title/Variant SKU) after its main row --
    # exactly how Shopify's export represents a product's 2nd/3rd photos.
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    sheet = next(r for r in rows if r["id"] == "8720828225332")
    assert sheet["image_link"] == "https://cdn.shopify.com/s/files/1/0000/0000/products/bamboo-fitted-sheet-sky-blue.jpg"
    assert sheet["additional_image_link"] == (
        "https://cdn.shopify.com/s/files/1/0000/0000/products/bamboo-fitted-sheet-sky-blue-2.jpg,"
        "https://cdn.shopify.com/s/files/1/0000/0000/products/bamboo-fitted-sheet-sky-blue-3.jpg"
    )


def test_load_products_from_csv_extracts_color_and_size_from_options():
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    sheet = next(r for r in rows if r["id"] == "8720828225332")
    assert sheet["color"] == "Sky Blue"
    assert sheet["size"] == "140x200 cm"


def test_load_products_from_csv_color_blank_when_no_color_option():
    # every other row in the fixture has no Option1/2 Name/Value at all --
    # confirms no title-parsed fallback ever kicks in.
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    others = [r for r in rows if r["id"] != "8720828225332"]
    assert all(r["color"] == "" and r["size"] == "" for r in others)


def test_load_products_from_csv_material_always_blank():
    # Shopify's default product export CSV carries no custom metafields --
    # material has no source in this adapter, ever.
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    assert all(r["material"] == "" for r in rows)


def test_csv_option_value_case_insensitive_and_checks_all_three_slots():
    raw = {"Option1 Name": "Pattern", "Option1 Value": "Striped",
           "Option2 Name": "COLOR", "Option2 Value": "Red",
           "Option3 Name": "", "Option3 Value": ""}
    assert build_feed._csv_option_value(raw, "color") == "Red"
    assert build_feed._csv_option_value(raw, "size") == ""


def test_load_products_from_csv_additional_image_link_blank_when_no_extra_images():
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    single_image_product = next(r for r in rows if r["id"] == "3333339014050")
    assert single_image_product["additional_image_link"] == ""


def test_collect_csv_images_caps_at_ten_and_dedupes():
    raw_rows = [{"Handle": "h", "Image Src": f"https://x/{i}.jpg", "Image Position": str(i)} for i in range(1, 13)]
    raw_rows.append({"Handle": "h", "Image Src": "https://x/1.jpg", "Image Position": "1"})  # duplicate
    images = build_feed._collect_csv_images(raw_rows)
    assert len(images["h"]) == 12  # collection itself isn't capped -- the caller applies MAX_ADDITIONAL_IMAGES
    assert images["h"][0] == "https://x/1.jpg"
    assert images["h"].count("https://x/1.jpg") == 1


def test_full_pipeline_end_to_end_counts(tmp_path):
    rows = build_feed.load_products_from_csv(str(FIXTURE_CSV))
    orig_data_dir, orig_reports_dir = build_feed.DATA_DIR, build_feed.REPORTS_DIR
    build_feed.DATA_DIR = tmp_path / "data"
    build_feed.REPORTS_DIR = tmp_path / "reports"
    build_feed.DATA_DIR.mkdir()
    build_feed.REPORTS_DIR.mkdir()
    try:
        stats = build_feed.run_pipeline(rows, "unit_test_feed", "unit test run")
        assert stats["rows_read"] == 11
        assert stats["accepted"] == 1
        assert stats["sample_rejected"] == 1
        assert len(stats["empty_description"]) == 1
        assert stats["feed_csv_path"].exists()
        assert stats["feed_txt_path"].exists()
        assert stats["exclusions_path"].exists()
        assert stats["report_path"].exists()

        header = stats["feed_csv_path"].read_text(encoding="utf-8").splitlines()[0]
        assert "additional_image_link" in header.split(",")
        assert {"color", "size", "material"} <= set(header.split(","))
        # the one accepted row (the bamboo fitted sheet) is the same handle
        # that carries the fixture's two extra image-only rows.
        body = stats["feed_csv_path"].read_text(encoding="utf-8")
        assert "bamboo-fitted-sheet-sky-blue-2.jpg" in body
        assert "bamboo-fitted-sheet-sky-blue-3.jpg" in body
    finally:
        build_feed.DATA_DIR, build_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir


# ---------------------------------------------------------------------------
# load_products_from_shopify_api (mocked -- no live credentials by design)
# ---------------------------------------------------------------------------
MOCK_GRAPHQL_RESPONSE = {
    "data": {
        "products": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "handle": "mocked-product",
                        "title": "Mocked Product",
                        "descriptionHtml": "<p>Mocked description.</p>",
                        "vendor": "Mock Vendor",
                        "status": "ACTIVE",
                        "featuredImage": {"url": "https://cdn.shopify.com/mocked.jpg"},
                        "translations": [
                            {"key": "title", "value": "Mockiertes Produkt"},
                            {"key": "body_html", "value": "<p>Mockierte Beschreibung.</p>"},
                        ],
                        "variants": {
                            "edges": [
                                {"node": {"sku": "MOCK-1", "price": "50.00", "barcode": "4006381333931", "inventoryQuantity": 5}}
                            ]
                        },
                    }
                }
            ],
        }
    }
}


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_mocked(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    graphql_response = MagicMock()
    graphql_response.json.return_value = MOCK_GRAPHQL_RESPONSE
    graphql_response.raise_for_status.return_value = None

    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "MOCK-1"
    assert row["title"] == "Mockiertes Produkt"
    assert row["description"] == "Mockierte Beschreibung."
    assert row["availability"] == "in_stock"
    assert row["gtin"] == "4006381333931"
    assert row["brand"] == "Mock Vendor"

    # First call is the OAuth token exchange, second is the GraphQL query.
    assert mock_post.call_count == 2
    assert "oauth/access_token" in mock_post.call_args_list[0].args[0]
    assert "graphql.json" in mock_post.call_args_list[1].args[0]
    assert row["additional_image_link"] == ""  # MOCK_GRAPHQL_RESPONSE's node carries no images field
    # MOCK_GRAPHQL_RESPONSE's node carries no fabricMetafield/selectedOptions
    assert row["color"] == ""
    assert row["size"] == ""
    assert row["material"] == ""


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_maps_color_size_material_from_real_shopify_data(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["fabricMetafield"] = {"value": "100% Bamboo (Tanboocel™)"}
    node["variants"] = {
        "edges": [
            {"node": {"sku": "MOCK-1", "price": "50.00", "barcode": "4006381333931", "inventoryQuantity": 5,
                      "selectedOptions": [{"name": "Size", "value": "140x200 cm"}, {"name": "Color", "value": "Coco white"}]}}
        ]
    }
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 1
    assert rows[0]["color"] == "Coco white"
    assert rows[0]["size"] == "140x200 cm"
    assert rows[0]["material"] == "100% Bamboo (Tanboocel™)"


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_color_blank_when_no_color_option(mock_post):
    """A product with only a Size option (several Casilin and Boomba Bedding
    Set products) must get color="" -- never a value parsed from the title,
    even though the title clearly contains a colour word."""
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["title"] = "Royal Percale Fitted Sheet - Beige"
    node["fabricMetafield"] = {"value": "100% percale cotton"}
    node["variants"] = {
        "edges": [
            {"node": {"sku": "SROYALPS434137", "price": "60.00", "barcode": "", "inventoryQuantity": 3,
                      "selectedOptions": [{"name": "Size", "value": "200 x 200 cm"}]}}
        ]
    }
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 1
    assert rows[0]["color"] == ""
    assert rows[0]["size"] == "200 x 200 cm"
    assert rows[0]["material"] == "100% percale cotton"


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_material_blank_when_no_fabric_metafield(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["fabricMetafield"] = None
    graphql_response_payload = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = graphql_response_payload
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert rows[0]["material"] == ""


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_builds_additional_image_link(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["images"] = {
        "nodes": [
            {"url": "https://cdn.shopify.com/mocked.jpg"},  # same as featuredImage -- must be excluded
            {"url": "https://cdn.shopify.com/mocked-2.jpg"},
            {"url": "https://cdn.shopify.com/mocked-3.jpg"},
            {"url": "https://cdn.shopify.com/mocked-2.jpg"},  # duplicate -- must not repeat
        ]
    }
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 1
    assert rows[0]["image_link"] == "https://cdn.shopify.com/mocked.jpg"
    assert rows[0]["additional_image_link"] == "https://cdn.shopify.com/mocked-2.jpg,https://cdn.shopify.com/mocked-3.jpg"


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_caps_additional_images_at_ten(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["images"] = {"nodes": [{"url": f"https://cdn.shopify.com/{i}.jpg"} for i in range(15)]}
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows[0]["additional_image_link"].split(",")) == 10


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_pagination_url_survives_products_with_images(mock_post):
    """Regression test: processing a product's `images` field used to reuse
    the local variable name `url`, clobbering the GraphQL endpoint URL that
    the *next* page's request needs. Every prior test mocks requests.post
    with a fixed side_effect list, which ignores whatever `url` argument is
    actually passed -- so this bug was invisible to the whole suite and only
    surfaced as a real 405 (POSTing to a Shopify CDN image URL) in production
    once real pagination with real HTTP calls ran. This test inspects the
    actual url argument on every call instead of ignoring it."""
    build_feed._cached_token = None
    graphql_endpoint = f"https://test-shop.myshopify.com/admin/api/{build_feed.API_VERSION}/graphql.json"

    node_with_images = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node_with_images["images"] = {"nodes": [{"url": "https://cdn.shopify.com/extra1.jpg"}, {"url": "https://cdn.shopify.com/extra2.jpg"}]}
    page1 = {"data": {"products": {"pageInfo": {"hasNextPage": True, "endCursor": "CURSOR1"},
                                    "edges": [{"node": node_with_images}]}}}
    page2 = MOCK_GRAPHQL_RESPONSE

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if len(calls) == 1:
            resp.json.return_value = {"access_token": "tok"}
        elif len(calls) == 2:
            resp.json.return_value = page1
        else:
            resp.json.return_value = page2
        return resp

    mock_post.side_effect = fake_post

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 2
    graphql_calls = calls[1:]
    assert all(u == graphql_endpoint for u in graphql_calls), f"expected every call to hit {graphql_endpoint}, got {calls}"


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_paginates(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    page1 = {
        "data": {
            "products": {
                "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR1"},
                "edges": [MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]],
            }
        }
    }
    page2 = MOCK_GRAPHQL_RESPONSE

    resp1 = MagicMock()
    resp1.json.return_value = page1
    resp1.raise_for_status.return_value = None
    resp2 = MagicMock()
    resp2.json.return_value = page2
    resp2.raise_for_status.return_value = None

    mock_post.side_effect = [token_response, resp1, resp2]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")
    assert len(rows) == 2
    assert mock_post.call_count == 3


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_no_de_translation_leaves_fields_blank(mock_post):
    """No fallback to the admin's English title/descriptionHtml when a DE
    translation is missing -- feed label is DE, so a blank here should hit
    validate_row's normal missing-field exclusion rather than silently
    publish English content under feed label DE."""
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["translations"] = []
    response = {
        "data": {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [{"node": node}],
            }
        }
    }
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None

    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret")

    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["description"] == ""


# ---------------------------------------------------------------------------
# market support (Belgium FR/NL expansion)
# ---------------------------------------------------------------------------
def test_unknown_market_raises_before_any_network_call():
    with pytest.raises(ValueError):
        build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret", market="fr-fr")


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_be_fr_uses_fr_locale_and_link_prefix(mock_post):
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["translations"] = [
        {"key": "title", "value": "Produit Simule"},
        {"key": "body_html", "value": "<p>Description simulee.</p>"},
    ]
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret", market="be-fr")

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Produit Simule"
    assert row["description"] == "Description simulee."
    assert row["link"].startswith("https://www.maisondecocon.com/fr/products/")
    assert row["translation_missing"] is False

    graphql_call = mock_post.call_args_list[1]
    assert graphql_call.kwargs["json"]["variables"]["locale"] == "fr"


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_flags_missing_translation_instead_of_blank(mock_post):
    """A product with no FR translation yet must be flagged, not silently
    published with blank title/description under the Belgium French feed."""
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["translations"] = []  # no FR translation exists yet for this product
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret", market="be-nl")

    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["description"] == ""
    assert rows[0]["translation_missing"] is True


# ---------------------------------------------------------------------------
# market support (English primary-locale expansion)
# ---------------------------------------------------------------------------
@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_en_reads_base_fields_not_translations(mock_post):
    """en is this shop's primary locale (confirmed via shopLocales) --
    Shopify never populates a translations() record for it, so the en
    market must read the base title/descriptionHtml fields instead. This
    also confirms it works even when translations comes back empty, which
    is the real, structural, every-single-time behavior for this locale
    (not an occasional gap)."""
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["translations"] = []  # real behavior for the primary locale, always
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret", market="en")

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Mocked Product"  # base field, not the (empty) translation
    assert row["description"] == "Mocked description."
    assert row["link"].startswith("https://www.maisondecocon.com/en/products/")
    assert row["translation_missing"] is False


@patch("build_feed.requests.post")
def test_load_products_from_shopify_api_en_blank_base_title_not_flagged_as_translation_missing(mock_post):
    """A genuinely blank base title/description under en must still fail
    ordinary validate_row (missing required field), not get mislabeled as
    a translation gap -- there's no translation concept for the primary
    locale, so this has to flow through the normal exclusion path."""
    build_feed._cached_token = None

    token_response = MagicMock()
    token_response.json.return_value = {"access_token": "mock-token"}
    token_response.raise_for_status.return_value = None

    node = dict(MOCK_GRAPHQL_RESPONSE["data"]["products"]["edges"][0]["node"])
    node["title"] = ""
    node["descriptionHtml"] = ""
    node["translations"] = []
    response = {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [{"node": node}]}}}
    graphql_response = MagicMock()
    graphql_response.json.return_value = response
    graphql_response.raise_for_status.return_value = None
    mock_post.side_effect = [token_response, graphql_response]

    rows = build_feed.load_products_from_shopify_api("test-shop.myshopify.com", "cid", "secret", market="en")

    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["translation_missing"] is False


def test_run_pipeline_excludes_and_reports_missing_translation_separately(tmp_path):
    row_ok = build_feed.ProductRow(
        id="OK-1", handle="ok-1", title="Titre", description="Desc", link="https://x",
        image_link="https://x.jpg", availability="in_stock", price="10.00 EUR", price_amount="10.00",
        brand="Brand", condition="new", gtin="4006381333931", mpn="OK-1", translation_missing=False,
    )
    row_missing = build_feed.ProductRow(
        id="MISS-1", handle="miss-1", title="", description="", link="https://x",
        image_link="https://x.jpg", availability="in_stock", price="10.00 EUR", price_amount="10.00",
        brand="Brand", condition="new", gtin="4006381333931", mpn="MISS-1", translation_missing=True,
    )
    orig_data_dir, orig_reports_dir = build_feed.DATA_DIR, build_feed.REPORTS_DIR
    build_feed.DATA_DIR = tmp_path / "data"
    build_feed.REPORTS_DIR = tmp_path / "reports"
    build_feed.DATA_DIR.mkdir()
    build_feed.REPORTS_DIR.mkdir()
    try:
        stats = build_feed.run_pipeline([row_ok, row_missing], "unit_test_market_feed", "unit test run")
        assert stats["accepted"] == 1
        assert len(stats["missing_translation"]) == 1
        assert stats["missing_translation"][0]["id"] == "MISS-1"
        feed_text = stats["feed_csv_path"].read_text(encoding="utf-8")
        assert "MISS-1" not in feed_text  # never silently included in the feed
    finally:
        build_feed.DATA_DIR, build_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir


def test_run_pipeline_report_filenames_dont_collide_across_markets(tmp_path):
    """Running de, be-fr, and be-nl on the same day must produce three
    distinct report files, not overwrite each other. DE keeps its exact
    existing bare-date filename (send_weekly_report.py depends on it)."""
    row = build_feed.ProductRow(
        id="X-1", handle="x-1", title="Titre", description="Desc", link="https://x",
        image_link="https://x.jpg", availability="in_stock", price="10.00 EUR", price_amount="10.00",
        brand="Brand", condition="new", gtin="4006381333931", mpn="X-1", translation_missing=False,
    )
    orig_data_dir, orig_reports_dir = build_feed.DATA_DIR, build_feed.REPORTS_DIR
    build_feed.DATA_DIR = tmp_path / "data"
    build_feed.REPORTS_DIR = tmp_path / "reports"
    build_feed.DATA_DIR.mkdir()
    build_feed.REPORTS_DIR.mkdir()
    try:
        today = build_feed.date.today().isoformat()
        stats_de = build_feed.run_pipeline([row], "google_merchant_feed", "de run", market="de")
        stats_fr = build_feed.run_pipeline([row], "google_merchant_feed_be_fr", "be-fr run", market="be-fr")
        stats_nl = build_feed.run_pipeline([row], "google_merchant_feed_be_nl", "be-nl run", market="be-nl")

        assert stats_de["report_path"].name == f"{today}.md"
        assert stats_fr["report_path"].name == f"{today}_be-fr.md"
        assert stats_nl["report_path"].name == f"{today}_be-nl.md"
        assert len({stats_de["report_path"], stats_fr["report_path"], stats_nl["report_path"]}) == 3
        assert all(p.exists() for p in (stats_de["report_path"], stats_fr["report_path"], stats_nl["report_path"]))
    finally:
        build_feed.DATA_DIR, build_feed.REPORTS_DIR = orig_data_dir, orig_reports_dir
