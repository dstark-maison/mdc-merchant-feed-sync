"""
Daily check (until 30 Nov 2026): do the 6 RIBECO Black Week variants still have the table prices on ribeco.de?
Reads https://www.ribeco.de/products.json (public Shopify feed), compares price (and compare_at) of the SKUs in
data/ribeco_bw_baseline.csv against the table, and emails via Resend when anything differs or the fetch fails.
Nothing is changed in Shopify or the feed by this job.

  python ribeco_price_check.py           # prints; mails only on a difference or a failure
  python ribeco_price_check.py --always  # also mails an all-OK note (first run)
"""
import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path

import requests

BASELINE = Path(__file__).parent / "data" / "ribeco_bw_baseline.csv"


def fetch_variants(pages=10):
    out = {}
    for page in range(1, pages + 1):
        r = requests.get(f"https://www.ribeco.de/products.json?limit=250&page={page}", timeout=60,
                         headers={"User-Agent": "Mozilla/5.0 (mdc-price-check)"})
        r.raise_for_status()
        products = r.json().get("products", [])
        if not products:
            break
        for p in products:
            for v in p["variants"]:
                out[str(v.get("sku") or "").strip()] = (v["price"], v.get("compare_at_price"))
    return out


def compare(rows, ribeco):
    """-> list of human-readable differences (empty = all as in the table)."""
    diffs = []
    for r in rows:
        got = ribeco.get(r["sku"])
        if got is None:
            diffs.append(f"{r['sku']}: not found on ribeco.de any more")
            continue
        price, cmp_ = got
        if Decimal(str(price)) != Decimal(r["sale"]):
            diffs.append(f"{r['sku']}: ribeco.de price {price} (table sale price {r['sale']}, regular {r['regular']})")
        elif cmp_ not in (None, "") and Decimal(str(cmp_)) != Decimal(r["regular"]):
            diffs.append(f"{r['sku']}: price ok ({price}) but ribeco.de compare_at {cmp_} differs from table regular {r['regular']}")
    return diffs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--always", action="store_true", help="also send a mail when everything matches")
    args = ap.parse_args(argv)
    from send_weekly_report import send_via_resend
    with open(BASELINE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    try:
        diffs = compare(rows, fetch_variants())
    except Exception as e:
        send_via_resend("ribeco.de price check FAILED", f"{type(e).__name__}: {e}")
        raise
    if diffs:
        body = ("ribeco.de prices differ from the approved Black Week table (data/ribeco_bw_baseline.csv):\n\n" + "\n".join(diffs) +
                "\n\nShopify prices are NOT changed automatically. Decide whether to adjust the table / Shopify / feed.")
        print(body)
        send_via_resend("ribeco.de price check: DIFFERENCE", body)
    else:
        print(f"OK: all {len(rows)} variants match the table.")
        if args.always:
            send_via_resend("ribeco.de price check: OK", f"All {len(rows)} tracked RIBECO variants match the Black Week table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
