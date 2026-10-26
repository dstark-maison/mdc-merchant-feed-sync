"""Tests for black_week.py: rules, Shopify-identical rounding, Berlin-time window, GMC sale fields, idealo price."""
import csv
import sys
from datetime import datetime, timezone
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


def test_gmc_sale_fields_before_and_inside_window():
    for now in (berlin(2026, 10, 27, 6, 0), berlin(2026, 11, 22, 6, 0), berlin(2026, 11, 25, 12, 0)):
        assert black_week.gmc_sale_fields(_row(), now) == ("26.96 EUR", EFFECTIVE)


def test_gmc_sale_fields_empty_after_window_and_for_other_rows():
    assert black_week.gmc_sale_fields(_row(), berlin(2026, 12, 1, 6, 0)) == ("", "")
    assert black_week.gmc_sale_fields(_row("Boomba Bamboo"), berlin(2026, 11, 25)) == ("", "")
    ocean = _row("Coco & Cici", handle="bamboo-duvet-cover-ocean-blue")
    assert black_week.gmc_sale_fields(ocean, berlin(2026, 11, 25)) == ("", "")


# ---- idealo price ----------------------------------------------------------------------------------------------
def test_idealo_price_discounted_only_inside_window():
    assert black_week.idealo_price(_row(), berlin(2026, 11, 22, 23, 59)) == "29.95"
    assert black_week.idealo_price(_row(), berlin(2026, 11, 23, 0, 0)) == "26.96"
    assert black_week.idealo_price(_row(), berlin(2026, 11, 30, 23, 59)) == "26.96"
    assert black_week.idealo_price(_row(), berlin(2026, 12, 1, 0, 0)) == "29.95"
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


def test_gmc_pipeline_sale_columns_empty_after_window(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "t_out", berlin(2026, 12, 2, 12, 0))
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
    after = _run_idealo(tmp_path, monkeypatch, "i_after", berlin(2026, 12, 1, 0, 5))
    vv = lambda rows: [r for r in rows if r["sku"].endswith("-VV")]
    other = lambda rows: [r for r in rows if not r["sku"].endswith("-VV")]
    assert vv(inside) and all(r["price"] == "26.96" for r in vv(inside))
    assert all(r["price"] == "29.95" for r in vv(before) + vv(after))
    base_prices = {r["sku"]: r["price"] for r in other(before)}
    assert other(inside) and all(r["price"] == base_prices[r["sku"]] for r in other(inside))
