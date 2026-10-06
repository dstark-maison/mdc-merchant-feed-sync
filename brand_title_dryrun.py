#!/usr/bin/env python3
"""
Dry run for the brands.yaml mapping: applies build_feed.apply_brand_mapping() to
the last built feeds (data/*.csv: the four GMC markets and idealo, built with
brand=vendor and the raw localized title) and writes a before/after row for every
item whose title or brand would change. Writes nothing into data/.

Usage: python brand_title_dryrun.py [--out ../output/feed_brand_title_diff.csv]
"""
import argparse
import collections
import csv
from pathlib import Path

import build_feed

SOURCES = [  # (feed, market key, language, data/ basename)
    ("gmc", "de", "de", "google_merchant_feed"),
    ("gmc", "be-fr", "fr", "google_merchant_feed_be_fr"),
    ("gmc", "be-nl", "nl", "google_merchant_feed_be_nl"),
    ("gmc", "en", "en", "google_merchant_feed_en"),
    ("idealo", "idealo", "de", "idealo_feed"),
]
COLUMNS = ["feed", "market", "language", "id", "item_group_id", "vendor", "brand_before", "brand_after",
           "title_before", "title_after", "title_len_after", "title_changed", "brand_changed",
           "overlap_merged", "flag"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(build_feed.ROOT.parent / "output" / "feed_brand_title_diff.csv"))
    args = parser.parse_args()

    diff, items, unknown = [], collections.Counter(), collections.Counter()
    for feed, market, lang, base in SOURCES:
        with open(build_feed.DATA_DIR / f"{base}.csv", newline="", encoding="utf-8") as f:
            for raw in csv.DictReader(f):
                if feed == "idealo":  # idealo columns: sku/brand/title
                    raw = {**raw, "id": raw["sku"], "item_group_id": raw["sku"]}
                items[market] += 1
                new, is_unknown = build_feed.apply_brand_mapping(build_feed.ProductRow(raw))
                if is_unknown:
                    unknown[new["vendor"] or "(empty vendor)"] += 1
                title_changed = new["title"] != raw["title"]
                brand_changed = new["brand"] != raw["brand"]
                if not (title_changed or brand_changed):
                    continue
                merged = title_changed and new["title"] != f"{new['brand']} {raw['title']}"
                flag = []
                if is_unknown:
                    flag.append("unknown-vendor")
                if len(new["title"]) > 150:
                    flag.append("over-150")
                diff.append({
                    "feed": feed, "market": market, "language": lang, "id": raw["id"],
                    "item_group_id": raw["item_group_id"], "vendor": new["vendor"],
                    "brand_before": raw["brand"], "brand_after": new["brand"],
                    "title_before": raw["title"], "title_after": new["title"],
                    "title_len_after": len(new["title"]), "title_changed": int(title_changed),
                    "brand_changed": int(brand_changed), "overlap_merged": int(merged), "flag": ";".join(flag),
                })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(diff)
    print(f"wrote {len(diff)} changed rows -> {out}")

    print("\nper feed/market: items / title changed / brand changed / overlap merged")
    for _, market, _, _ in SOURCES:
        rows = [d for d in diff if d["market"] == market]
        print(f"  {market:7} {items[market]:4} / {sum(d['title_changed'] for d in rows):4} / "
              f"{sum(d['brand_changed'] for d in rows):4} / {sum(d['overlap_merged'] for d in rows):4}")
    for market in ("de", "idealo"):
        print(f"\nper vendor ({market}): titles changed / merged")
        for vendor in sorted({d["vendor"] for d in diff if d["market"] == market}):
            rows = [d for d in diff if d["market"] == market and d["vendor"] == vendor]
            print(f"  {vendor:16} {sum(d['title_changed'] for d in rows):4} / {sum(d['overlap_merged'] for d in rows):4}")
    lens = [d["title_len_after"] for d in diff if d["title_changed"]]
    print(f"\nnew title length: max {max(lens, default=0)}, over 150: {sum(l > 150 for l in lens)}")
    print(f"unknown vendors in feeds: {dict(unknown) or 'none'}")

    for market in ("de", "idealo"):
        merged = [d for d in diff if d["overlap_merged"] and d["market"] == market]
        print(f"\nmerged titles, {market}: {len(merged)}")
        for d in merged[:5]:
            print(f"  {d['title_before']}  =>  {d['title_after']}")
    return diff


if __name__ == "__main__":
    main()
