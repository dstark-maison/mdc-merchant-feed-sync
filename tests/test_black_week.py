"""Tests for black_week.py: rules, Shopify-identical rounding, Berlin-time window, GMC sale fields, idealo price."""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import black_week  # noqa: E402
import build_feed  # noqa: E402
from black_week import BERLIN  # noqa: E402

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_products_export.csv"
EFFECTIVE = "2026-11-23T00:00+0100/2026-11-30T23:59+0100"


def berlin(*args):
    return datetime(*args, tzinfo=BERLIN)


# ---- rules -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("vendor,title,handle,pct", [
    ("VIVARAISE", "Cotton Velvet Cushion Fara", "x", 10),
    ("MoST Blankets", "Wool Throw", "x", 10),
    ("SalesFever", "Upholstered Bed", "x", 10),
    ("Coco & Cici", "Tencel Twill Duvet Cover White", "tencel-twill-duvet-cover-white", 5),
    ("Coco & Cici", "Bamboo Duvet Cover Ocean Blue", "bamboo-duvet-cover-ocean-blue", 0),
    # translated market feed: localized title, base handle still identifies Ocean Blue
    ("Coco & Cici", "Bettbezug aus Bambus, Ozeanblau", "bamboo-duvet-cover-ocean-blue", 0),
    ("Boomba Bamboo", "Bamboo Bedding Set", "x", 0),
    ("Ángel Cerdá S.L.", "Whatever", "x", 0),
    ("", "No vendor", "x", 0),
])
def test_discount_pct(vendor, title, handle, pct):
    assert black_week.discount_pct(vendor, title, handle) == pct


# ---- rounding: identical to Shopify (verified with draftOrderCalculate) -------------------------------------------
@pytest.mark.parametrize("price,pct,expected", [
    ("29.95", 10, "26.96"), ("29.90", 5, "28.41"), ("0.15", 10, "0.14"), ("219.95", 5, "208.96"),
    ("108.90", 10, "98.01"), ("88.90", 10, "80.01"), ("71.00", 10, "63.90"), ("139.95", 5, "132.96"),
])
def test_discounted_amount_matches_shopify_rounding(price, pct, expected):
    assert black_week.discounted_amount(price, pct) == expected


def test_discounted_amount_unusable_input():
    assert black_week.discounted_amount("", 10) == ""
    assert black_week.discounted_amount("abc", 10) == ""
    assert black_week.discounted_amount("0", 10) == ""
    assert black_week.discounted_amount("10.00", 0) == ""


# ---- window (Europe/Berlin) ------------------------------------------------------------------------------------
@pytest.mark.parametrize("now,inside", [
    (berlin(2026, 11, 22, 23, 59), False),
    (berlin(2026, 11, 23, 0, 0), True),
    (berlin(2026, 11, 30, 23, 59), True),
    (berlin(2026, 12, 1, 0, 0), False),
])
def test_in_window(now, inside):
    assert black_week.in_window(now) is inside


def test_in_window_converts_other_timezones():
    assert black_week.in_window(datetime(2026, 11, 22, 23, 5, tzinfo=timezone.utc)) is True    # 00:05 Berlin, 23 Nov
    assert black_week.in_window(datetime(2026, 11, 22, 22, 59, tzinfo=timezone.utc)) is False  # 23:59 Berlin, 22 Nov
    assert black_week.in_window(datetime(2026, 11, 30, 22, 59, tzinfo=timezone.utc)) is True   # 23:59 Berlin, 30 Nov
    assert black_week.in_window(datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)) is False   # 00:00 Berlin, 1 Dec


def test_effective_date_range():
    assert black_week.effective_date_range() == EFFECTIVE


# ---- GMC sale fields -------------------------------------------------------------------------------------------
def _row(vendor="VIVARAISE", price="29.95", title="Cushion", handle="cushion"):
    return build_feed.ProductRow(vendor=vendor, brand=vendor, title=title, handle=handle, price_amount=price,
                                 price=f"{price} EUR")


def test_gmc_sale_fields_published_from_the_eve_and_inside_window():
    for now in (berlin(2026, 11, 22, 0, 0), berlin(2026, 11, 22, 6, 0), berlin(2026, 11, 25, 12, 0)):
        assert black_week.gmc_sale_fields(_row(), now) == ("26.96 EUR", EFFECTIVE)


def test_gmc_sale_fields_empty_before_publish_from():
    for now in (berlin(2026, 10, 27, 6, 0), berlin(2026, 11, 21, 23, 59), berlin(2026, 11, 21, 23, 59, 59)):
        assert black_week.gmc_sale_fields(_row(), now) == ("", "")
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 11, 22, 0, 0)) == ("26.96 EUR", EFFECTIVE)


def test_publish_from_boundary_is_timezone_and_dst_safe():
    # 22 Nov is CET (UTC+1): 00:00 Berlin = 21 Nov 23:00 UTC. The clocks went back on 25 Oct (CEST -> CET).
    assert black_week.PUBLISH_FROM.utcoffset().total_seconds() == 3600
    assert black_week.gmc_sale_fields(_row(), datetime(2026, 11, 21, 22, 59, 59, tzinfo=timezone.utc)) == ("", "")
    assert black_week.gmc_sale_fields(_row(), datetime(2026, 11, 21, 23, 0, tzinfo=timezone.utc)) == ("26.96 EUR", EFFECTIVE)
    # a UTC+2 (summer time) clock reading of 22 Nov 00:30 is 21 Nov 22:30 UTC = still before the boundary
    plus2 = timezone(timedelta(hours=2))
    assert black_week.gmc_sale_fields(_row(), datetime(2026, 11, 22, 0, 30, tzinfo=plus2)) == ("", "")
    # the last CEST day (24 Oct, UTC+2) and the first CET day (25 Oct 03:00) are both before the boundary
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 10, 24, 23, 59)) == ("", "")
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 10, 25, 3, 30)) == ("", "")


def test_gmc_sale_fields_empty_after_window_and_for_other_rows():
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 12, 26, 6, 0)) == ("", "")
    assert black_week.gmc_sale_fields(_row("Boomba Bamboo"), berlin(2026, 11, 25)) == ("", "")
    ocean = _row("Coco & Cici", handle="bamboo-duvet-cover-ocean-blue")
    assert black_week.gmc_sale_fields(ocean, berlin(2026, 11, 25)) == ("", "")


# ---- idealo price ----------------------------------------------------------------------------------------------
def test_idealo_price_discounted_only_inside_window():
    assert black_week.idealo_price(_row(), berlin(2026, 11, 22, 23, 59)) == "29.95"
    assert black_week.idealo_price(_row(), berlin(2026, 11, 23, 0, 0)) == "26.96"
    assert black_week.idealo_price(_row(), berlin(2026, 11, 30, 23, 59)) == "26.96"
    assert black_week.idealo_price(_row(), berlin(2026, 12, 26, 0, 0)) == "29.95"
    assert black_week.idealo_price(_row("Boomba Bamboo"), berlin(2026, 11, 25)) == "29.95"


# ---- end to end through the GMC pipeline -----------------------------------------------------------------------
def _rows_boomba_and_vivaraise_copy():
    out = []
    for r in build_feed.load_products_from_csv(str(FIXTURE_CSV)):
        if (r.get("vendor") or r.get("brand")) == "Boomba Bamboo":
            out.append(build_feed.ProductRow(r))
            w = build_feed.ProductRow(r)
            w["vendor"] = w["brand"] = "VIVARAISE"
            w["id"] = w["id"] + "-VV"
            w["price_amount"], w["price"] = "29.95", "29.95 EUR"
            out.append(w)
    assert out
    return out


def _run(tmp_path, monkeypatch, name, now):
    monkeypatch.setattr(build_feed, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(build_feed, "REPORTS_DIR", tmp_path / "reports")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "reports").mkdir(exist_ok=True)
    build_feed.run_pipeline(_rows_boomba_and_vivaraise_copy(), name, "test", now=now)
    return list(csv.DictReader(open(tmp_path / "data" / f"{name}.csv", encoding="utf-8")))


def test_gmc_pipeline_writes_sale_columns_inside_window(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "t_in", berlin(2026, 11, 25, 12, 0))
    assert "sale_price" in rows[0] and "sale_price_effective_date" in rows[0]
    vv = [r for r in rows if r["id"].endswith("-VV")]
    other = [r for r in rows if not r["id"].endswith("-VV")]
    assert vv and all(r["sale_price"] == "26.96 EUR" and r["price"] == "29.95 EUR" for r in vv)
    assert all(r["sale_price_effective_date"] == EFFECTIVE for r in vv)
    assert other and all(r["sale_price"] == "" and r["sale_price_effective_date"] == "" for r in other)


def test_gmc_pipeline_sale_columns_empty_before_publish_from(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "t_pre", berlin(2026, 11, 21, 23, 59))
    assert rows and all(r["sale_price"] == "" and r["sale_price_effective_date"] == "" for r in rows)
    rows = _run(tmp_path, monkeypatch, "t_eve", berlin(2026, 11, 22, 0, 0))
    vv = [r for r in rows if r["id"].endswith("-VV")]
    assert vv and all(r["sale_price"] == "26.96 EUR" for r in vv)


def test_gmc_pipeline_sale_columns_empty_after_window(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "t_out", berlin(2026, 12, 27, 12, 0))
    assert rows and all(r["sale_price"] == "" and r["sale_price_effective_date"] == "" for r in rows)


# ---- end to end through the idealo pipeline --------------------------------------------------------------------
def _run_idealo(tmp_path, monkeypatch, name, now):
    import idealo_feed
    monkeypatch.setattr(idealo_feed, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(idealo_feed, "REPORTS_DIR", tmp_path / "reports")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "reports").mkdir(exist_ok=True)
    idealo_feed.run_idealo_pipeline(_rows_boomba_and_vivaraise_copy(), {}, name, "test", now=now)
    return list(csv.DictReader(open(tmp_path / "data" / f"{name}.csv", encoding="utf-8")))


def test_idealo_pipeline_price_inside_and_outside_window(tmp_path, monkeypatch):
    inside = _run_idealo(tmp_path, monkeypatch, "i_in", berlin(2026, 11, 23, 0, 5))
    before = _run_idealo(tmp_path, monkeypatch, "i_before", berlin(2026, 11, 22, 6, 0))
    after = _run_idealo(tmp_path, monkeypatch, "i_after", berlin(2026, 12, 26, 0, 5))
    vv = lambda rows: [r for r in rows if r["sku"].endswith("-VV")]
    other = lambda rows: [r for r in rows if not r["sku"].endswith("-VV")]
    assert vv(inside) and all(r["price"] == "26.96" for r in vv(inside))
    assert all(r["price"] == "29.95" for r in vv(before) + vv(after))
    base_prices = {r["sku"]: r["price"] for r in other(before)}
    assert other(inside) and all(r["price"] == base_prices[r["sku"]] for r in other(inside))


# ---- Christmas 2026 window (1-25 Dec, flat 5 % for every Black Week product) ------------------------------------
XMAS_EFFECTIVE = "2026-12-01T00:00+0100/2026-12-25T23:59+0100"


@pytest.mark.parametrize("vendor,title,handle,pct", [
    ("VIVARAISE", "Cotton Velvet Cushion Fara", "x", 5),
    ("MoST Blankets", "Wool Throw", "x", 5),
    ("SalesFever", "Upholstered Bed", "x", 5),
    ("Coco & Cici", "Tencel Twill Duvet Cover White", "tencel-twill-duvet-cover-white", 5),
    ("Coco & Cici", "Bettbezug aus Bambus, Ozeanblau", "bamboo-duvet-cover-ocean-blue", 0),
    ("Boomba Bamboo", "Bamboo Bedding Set", "x", 0),
    ("", "No vendor", "x", 0),
])
def test_christmas_pct(vendor, title, handle, pct):
    assert black_week.christmas_pct(vendor, title, handle) == pct


@pytest.mark.parametrize("now,inside", [
    (berlin(2026, 11, 30, 23, 59), False),
    (berlin(2026, 12, 1, 0, 0), True),
    (berlin(2026, 12, 25, 23, 59), True),
    (berlin(2026, 12, 26, 0, 0), False),
])
def test_christmas_in_window(now, inside):
    assert black_week.christmas_in_window(now) is inside


def test_christmas_effective_date_range():
    assert black_week.christmas_effective_date_range() == XMAS_EFFECTIVE
    assert black_week.effective_date_range() == EFFECTIVE  # Black Week window unchanged


def test_gmc_sale_fields_christmas_window():
    # 29.95 -5 % = 29.95 - 1.49 (truncated) = 28.46; Black Week would give 26.96
    for now in (berlin(2026, 12, 1, 0, 0), berlin(2026, 12, 15, 12, 0), berlin(2026, 12, 25, 23, 59)):
        assert black_week.gmc_sale_fields(_row(), now) == ("28.46 EUR", XMAS_EFFECTIVE)
    # published ahead: between the windows the row already carries the Christmas sale
    assert black_week.gmc_sale_fields(_row("Coco & Cici", "139.95"), berlin(2026, 12, 1, 0, 0)) == ("132.96 EUR", XMAS_EFFECTIVE)
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 12, 26, 0, 0)) == ("", "")
    assert black_week.gmc_sale_fields(_row("Boomba Bamboo"), berlin(2026, 12, 10)) == ("", "")
    ocean = _row("Coco & Cici", handle="bamboo-duvet-cover-ocean-blue")
    assert black_week.gmc_sale_fields(ocean, berlin(2026, 12, 10)) == ("", "")


def test_idealo_price_christmas_window():
    assert black_week.idealo_price(_row(), berlin(2026, 11, 30, 23, 59)) == "26.96"   # still Black Week
    assert black_week.idealo_price(_row(), berlin(2026, 12, 1, 0, 0)) == "28.46"
    assert black_week.idealo_price(_row(), berlin(2026, 12, 25, 23, 59)) == "28.46"
    assert black_week.idealo_price(_row(), berlin(2026, 12, 26, 0, 0)) == "29.95"
    assert black_week.idealo_price(_row("Boomba Bamboo"), berlin(2026, 12, 10)) == "29.95"


def test_gmc_pipeline_christmas_window(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "t_xmas", berlin(2026, 12, 10, 12, 0))
    vv = [r for r in rows if r["id"].endswith("-VV")]
    other = [r for r in rows if not r["id"].endswith("-VV")]
    assert vv and all(r["sale_price"] == "28.46 EUR" and r["price"] == "29.95 EUR" for r in vv)
    assert all(r["sale_price_effective_date"] == XMAS_EFFECTIVE for r in vv)
    assert other and all(r["sale_price"] == "" for r in other)


def test_idealo_pipeline_christmas_window(tmp_path, monkeypatch):
    inside = _run_idealo(tmp_path, monkeypatch, "i_xmas", berlin(2026, 12, 10, 12, 0))
    after = _run_idealo(tmp_path, monkeypatch, "i_xmas_after", berlin(2026, 12, 26, 0, 5))
    vv = lambda rows: [r for r in rows if r["sku"].endswith("-VV")]
    assert vv(inside) and all(r["price"] == "28.46" for r in vv(inside))
    assert vv(after) and all(r["price"] == "29.95" for r in vv(after))
