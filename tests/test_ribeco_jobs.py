"""RIBECO Black Week revert + ribeco.de price check: the pure decision logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import black_week  # noqa: E402
import ribeco_bw_revert as rev  # noqa: E402
import ribeco_price_check as chk  # noqa: E402

ROW = {"sku": "6192972677", "regular": "539.00", "sale": "323.40", "cost_regular": "339.71"}


def test_baseline_has_the_six_live_variants_and_matches_the_feed_table():
    rows = rev.load_baseline()
    assert len(rows) == 6 and len({r["variant_id"] for r in rows}) == 6
    assert {r["sku"]: (r["regular"], r["sale"]) for r in rows} == black_week.ribeco_prices()


def test_classify():
    assert rev.classify(ROW, "323.40", "539.00", "203.82") == "needs_revert"   # sale state
    assert rev.classify(ROW, "539.00", None, "339.71") == "reverted"            # already back
    assert rev.classify(ROW, "539.00", None, "203.82") == "needs_revert"        # price back, cost still the sale cost
    assert rev.classify(ROW, "500.00", None, "339.71") == "unexpected"          # manual edit: leave alone


def test_apply_is_refused_before_the_window_end(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.delenv("FORCE", raising=False)
    monkeypatch.setattr(rev, "_now", lambda: datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))   # fixed clock: stays valid after 30 Nov
    assert rev.main(["--apply"]) == 2


def test_price_check_compare():
    rows = rev.load_baseline()
    ok = {r["sku"]: (r["sale"], r["regular"]) for r in rows}
    assert chk.compare(rows, ok) == []
    changed = dict(ok, **{"6192972677": ("350.00", "539.00")})
    assert len(chk.compare(rows, changed)) == 1 and "350.00" in chk.compare(rows, changed)[0]
    gone = {k: v for k, v in ok.items() if k != "6192971283"}
    assert "not found" in chk.compare(rows, gone)[0]
    cmp_changed = dict(ok, **{"6192972678": ("359.40", "649.00")})
    assert "compare_at" in chk.compare(rows, cmp_changed)[0]
