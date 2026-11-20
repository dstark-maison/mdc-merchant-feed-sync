"""
Revert the 6 RIBECO Black Week variants after the window (Black Week 2026, ends 30 Nov 23:59 Europe/Berlin).

For each variant in data/ribeco_bw_baseline.csv (explicit variant ids of the LIVE products; the draft Collective
duplicate "Gelschaum-Topper" shares the SKUs and is never touched): price -> regular, compare_at -> none,
unit cost -> cost_regular. Idempotent. A variant whose price is neither the sale nor the regular price (a manual edit)
is left alone and reported by email. Nothing is written without --apply, and --apply refuses to run before
NOT_BEFORE unless FORCE=1 (so a stray dispatch cannot end the sale early).

  python ribeco_bw_revert.py            # dry run: print what would change
  python ribeco_bw_revert.py --apply

Needs SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET (app with write_products + write_inventory) and,
for the alert email, RESEND_API_KEY. The workflow triggers build-feeds afterwards.
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

BASELINE = Path(__file__).parent / "data" / "ribeco_bw_baseline.csv"
NOT_BEFORE = datetime(2026, 11, 30, 22, 55, tzinfo=timezone.utc)   # 23:55 Berlin
API = "2025-07"

NODES_QUERY = """query($ids:[ID!]!){ nodes(ids:$ids){ ... on ProductVariant{
    id sku price compareAtPrice inventoryItem{ id unitCost{ amount } } } } }"""
PRICE_MUTATION = """mutation($p:ID!,$v:[ProductVariantsBulkInput!]!){
    productVariantsBulkUpdate(productId:$p, variants:$v){ userErrors{ field message } } }"""
COST_MUTATION = """mutation($i:ID!,$c:InventoryItemInput!){
    inventoryItemUpdate(id:$i, input:$c){ userErrors{ field message } } }"""


def _now():
    return datetime.now(timezone.utc)


def load_baseline(path=BASELINE):
    with open(path, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f, delimiter=";")]


def classify(row, price, compare_at, cost):
    """'reverted' | 'needs_revert' | 'unexpected' for one variant, from its live price / compare_at / unit cost."""
    reg, sale = Decimal(row["regular"]), Decimal(row["sale"])
    p = Decimal(str(price))
    c = Decimal(str(cost)) if cost not in (None, "") else None
    if p == reg and compare_at in (None, "") and c == Decimal(row["cost_regular"]):
        return "reverted"
    if p in (reg, sale):
        return "needs_revert"
    return "unexpected"


def _token(domain, cid, secret):
    r = requests.post(f"https://{domain}/admin/oauth/access_token", timeout=30,
                      data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
    r.raise_for_status()
    return r.json()["access_token"]


def _gql(domain, token, query, variables=None):
    r = requests.post(f"https://{domain}/admin/api/{API}/graphql.json", timeout=60,
                      headers={"X-Shopify-Access-Token": token}, json={"query": query, "variables": variables or {}})
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(f"GraphQL errors: {d['errors']}")
    return d["data"]


def alert(subject, body):
    try:
        from send_weekly_report import send_via_resend
        send_via_resend(subject, body)
    except Exception as e:  # never hide the original problem behind a mail failure
        print(f"ALERT MAIL FAILED: {e}\n{subject}\n{body}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.apply and _now() < NOT_BEFORE and os.environ.get("FORCE") != "1":
        print(f"REFUSED: --apply before {NOT_BEFORE.isoformat()} (set FORCE=1 to override).")
        return 2
    domain, cid, secret = (os.environ.get(k) for k in ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"))
    if not (domain and cid and secret):
        print("ERROR: SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET must be set")
        return 1
    try:
        tok = _token(domain, cid, secret)
        rows = load_baseline()
        data = _gql(domain, tok, NODES_QUERY, {"ids": [f"gid://shopify/ProductVariant/{r['variant_id']}" for r in rows]})
        live = {n["id"].rsplit("/", 1)[1]: n for n in data["nodes"] if n}
        problems, todo = [], []
        for r in rows:
            n = live.get(r["variant_id"])
            if not n or n["sku"] != r["sku"]:
                problems.append(f"{r['sku']}: variant {r['variant_id']} not found / SKU mismatch")
                continue
            cost = (n["inventoryItem"].get("unitCost") or {}).get("amount")
            state = classify(r, n["price"], n["compareAtPrice"], cost)
            print(f"{r['sku']} price={n['price']} compare_at={n['compareAtPrice']} cost={cost} -> {state}")
            if state == "unexpected":
                problems.append(f"{r['sku']}: price {n['price']} is neither sale {r['sale']} nor regular {r['regular']} -- left alone")
            elif state == "needs_revert":
                todo.append((r, n))
        if args.apply:
            for r, n in todo:
                res = _gql(domain, tok, PRICE_MUTATION, {
                    "p": f"gid://shopify/Product/{r['product_id']}",
                    "v": [{"id": n["id"], "price": r["regular"], "compareAtPrice": None}]})
                errs = list(res["productVariantsBulkUpdate"]["userErrors"])
                res2 = _gql(domain, tok, COST_MUTATION, {"i": n["inventoryItem"]["id"], "c": {"cost": r["cost_regular"]}})
                errs += res2["inventoryItemUpdate"]["userErrors"]
                if errs:
                    problems.append(f"{r['sku']}: write errors {errs}")
                else:
                    print(f"{r['sku']}: reverted to {r['regular']} (cost {r['cost_regular']}, compare_at removed)")
        else:
            print(f"DRY RUN: {len(todo)} variant(s) would be reverted.")
        if problems:
            alert("RIBECO Black Week revert: PROBLEMS", "\n".join(problems))
            print("\n".join(problems))
            return 1
        return 0
    except Exception as e:
        alert("RIBECO Black Week revert: FAILED", f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
