# GMC non-visible-products investigation — proposal (NOT YET APPLIED)

**Resolved (2026-09-10):** MoST GTIN backfill (11 variants) and the DE/FR/NL
`link_handle` fix are both applied and live — see commits `7a3ecc6` / `25bdb97`.

Live Shopify re-fetch, 2026-09-10. No writes made to Shopify or to build_feed.py.
CSVs referenced below are in this same `reports/` folder.

## Scope correction (read this first)

The brief's counts (22 missing-GTIN / 19 Boomba+2 Casilin+1 MoST, 9 DE-link-mismatch)
do not match live state. Whatever export those numbers came from is stale or was a
sample, not the full picture:

| Vendor | Brief said | Live count (active variants) |
|---|---|---|
| Boomba Bamboo | 19 missing GTIN | **370 active variants total** — 320 backfillable from `sku`, 50 genuinely have no GTIN anywhere |
| Casilin | 2 missing GTIN | **19 active variants** with `barcode` null (SKUs are non-numeric internal codes, not GTIN candidates) |
| MoST Blankets | 1 missing GTIN | **16 active variants** with `barcode` null — 11 backfillable, 3 checksum-fail, 2 are a duplicate-GTIN conflict |

Task B's "9 DE products" claim is addressed separately below — it's flagged as
**refuted**, not just recounted.

---

## Task A — GTIN backfill

### Root cause (per Task A's ask: already-present-but-unmapped vs. genuinely-absent)

`build_feed.py`'s field mapping is fine — `gtin=barcode if gtin_checksum_valid(barcode) else ""`
(line 564) already pulls `barcode` correctly whenever it's set. The gap is upstream, in
Shopify data, and splits into two very different situations:

**(a) Mapping-fix-only — GTIN already exists in Shopify, just in the wrong field.**
For most single-item Boomba Bamboo products (fitted sheets, pillowcases, duvets, throws)
and most MoST Blankets throws, the `sku` field IS a valid, GS1-checksum-passing EAN-13 —
identical in form to the `barcode` values already present and correctly feeding on other
variants of the same vendors. `barcode` is simply null on these. This is not sourcing new
data — it's copying a real identifier from one Shopify field to another.

- `reports/gtin_backfill_boomba.csv` — 320 rows, `sku,proposed_barcode,handle,vendor`
- `reports/gtin_backfill_most.csv` — 11 rows, same shape

Sample (Boomba):
```
sku,proposed_barcode,handle,vendor
6154513585586,6154513585586,bamboo-fitted-sheet-lavender-mist-400tc,Boomba Bamboo
6154512472436,6154512472436,bamboo-fitted-sheet-lavender-mist-400tc,Boomba Bamboo
6154516032025,6154516032025,bamboo-pillowcases-lavender-mist-400tc,Boomba Bamboo
8720828225356,8720828225356,bamboo-fitted-sheet-for-mattress-topper-sky-blue-400tc,Boomba Bamboo
```
Sample (MoST):
```
sku,proposed_barcode,handle,vendor
8720849140577,8720849140577,wool-throw-blanket-chocolate-chip-140-200,MoST Blankets
8720849140553,8720849140553,wool-bed-blanket-hunter-140-200,MoST Blankets
0793002340693,0793002340693,wool-bed-blanket-misted-yellow-140-200,MoST Blankets
```
Checked: no duplicate GTIN across different products and no duplicate within a single
product's own variants in this 331-row set.

**(b) Genuinely absent — flagged, not fabricated.**
Every Boomba **Bedding Set** product (1-person-*, 2-person-*, white-bamboo-bedding-set-*,
light-green-bamboo-bedding-set-*) uses synthetic placeholder SKUs — repeated-digit blocks
like `9999998014050` / `9999999014050`, `8888886020060`, `2222218024060`. Two of these
(`8888884020060`, `88888820026060`) coincidentally pass the GS1 checksum despite the
obvious placeholder pattern; I did not treat a coincidental pass as evidence of a real
GTIN and flagged them anyway. One oddball (`bamboo-fitted-sheet-sky-blue-400tc`, sku
`8022025`) is 7 digits — too short to be a GTIN at all. All of Casilin's 19 null-barcode
variants are non-numeric internal codes (`FCRINKSTRF1652`, `STENCEL62`, etc.) — not GTIN
candidates by construction. 3 MoST SKUs (`8720849140594`, `8720849140570`,
`8720849140471`) look like real EAN-13s that fail checksum by one digit — plausible
transcription errors, but I won't guess the correct digit.

- `reports/gtin_flagged_boomba.csv` — 50 rows
- `reports/gtin_flagged_casilin.csv` — 19 rows
- `reports/gtin_flagged_most.csv` — 3 rows

I have no local Boomba Bamboo / Casilin / MoST Blankets supplier catalogs in this
project to source real GTINs from, and did not fabricate any. These 72 variants need
either the actual supplier price list/catalog, or your explicit go-ahead to attempt
public web lookups per-SKU (low-confidence, would need individual review, not a bulk
op) — your call.

**(c) Conflict — needs a decision, not a backfill.**
`0793002340679` (checksum-valid) is the SKU-derived candidate for **two different
products**: `wool-throw-blanket-cool-mint-140-200` (sku `0793002340679-copy`) and
`wool-bedspread-cool-mint-200-220` (sku `0793002340679`, no suffix). Same normalized
code, different sizes/products — either one is mislabeled or they're genuinely the same
physical item marketed under two listings. Pulled out of the backfill set.

- `reports/gtin_conflicts_most.csv` — 2 rows

### Proposed action
Approve `reports/gtin_backfill_boomba.csv` + `reports/gtin_backfill_most.csv` (331
variants total) as `variant.barcode` updates. I'd push via `--only` scoped to just
these 331 variant IDs — not run against the full catalog. No write happens until you
say go.

---

## Task B — DE link "mismatch": refuted, real bug found instead

### The brief's diagnosis is wrong — verified live, don't implement the requested fix

Fetched live:
- `https://www.maisondecocon.com/products/<handle>` (bare, DE's current `link_prefix: ""`)
  → HTTP 200, `<html lang="de">`, self-referencing `<link rel="canonical">`. This **is**
  the correct German page today.
- `https://www.maisondecocon.com/de/products/<handle>` → HTTP 200 but **redirects to the
  bare URL** — `/de/` is not a valid locale segment on this storefront at all.

Adding a `/de/` prefix, as the brief asks, would not fix anything — it would replace a
currently-direct, self-canonical DE link with one that 301-redirects on every single one
of the ~877 DE feed rows. That's a regression, not a fix, and could itself hurt Merchant
Center visibility (redirected landing pages are a known GMC soft-flag). **I did not
implement this.**

### What's actually broken (verified, affects far more than 9 SKUs, all 3 non-primary markets)

`load_products_from_shopify_api()` queries `translations(locale: $locale) { key value }`,
which **does** include a `handle` key when a product has a locale-specific URL slug (confirmed
live — e.g. product `dortmund-upholstered-bed-fabric-blue-grey-180x200` has a German
translation with `key: "handle", value: "dortmund-polsterbett-stoff-blaugrau-180x200"`).
But the code only ever reads `tr.get("title")` and `tr.get("body_html")` — never
`tr.get("handle")` — so `link` is built from the base (English) `handle` for every
market, including DE, FR, and NL alike.

Confirmed live impact — using the base handle in the URL still resolves (Shopify
301-redirects handle-only, staying on the same domain/locale), but it's an avoidable
redirect hop that Google would rather not see feed links carry, on however many products
have a distinct per-locale handle (not just DE, and not just 9 — 4 of the first 5
products checked had one):

| Product | Locale | Before (current code) | After (fix) |
|---|---|---|---|
| Dortmund bed | de | `.../products/dortmund-upholstered-bed-fabric-blue-grey-180x200` → 301 → correct page | `.../products/dortmund-polsterbett-stoff-blaugrau-180x200` (direct, self-canonical) |
| Hamburg bed | de | `.../products/hamburg-upholstered-bed-leatherette-brown-180x200` → 301 | `.../products/hamburg-polsterbett-kunstleder-braun-180x200` (direct) |
| Berlin bed | de | `.../products/berlin-upholstered-bed-fabric-light-grey-180x200` → 301 | `.../products/berlin-polsterbett-stoff-hellgrau-180x200` (direct) |
| Munich bed | de | `.../products/munich-upholstered-bed-fabric-light-grey-180x200` → 301 | `.../products/muenchen-polsterbett-stoff-hellgrau-180x200` (direct) |
| Bremen bed | fr | `.../fr/products/bremen-upholstered-bed-mink-leatherette-180x200` → 301 | `.../fr/products/lit-rembourre-bremen-similicuir-mink-180x200` (direct) |
| Bremen bed | de | (no German handle translation exists for this one) | unchanged — falls back to base handle, same as today |

### Proposed code fix

```diff
--- a/build_feed.py
+++ b/build_feed.py
@@ -513,12 +513,15 @@
         for edge in block["edges"]:
             node = edge["node"]
             handle = node["handle"]
             if is_primary:
                 title = node.get("title") or ""
                 description = strip_html(node.get("descriptionHtml") or "")
                 translation_missing = False  # no translation concept for the primary locale
+                link_handle = handle  # primary locale's own handle IS the base handle
             else:
                 tr = {t["key"]: t["value"] for t in node.get("translations") or []}
                 title = tr.get("title") or ""
                 description = strip_html(tr.get("body_html") or "")
                 translation_missing = not tr.get("title") or not tr.get("body_html")
+                # Shopify DOES return a locale-specific `handle` translation for
+                # products with a localized URL slug -- use it so `link` resolves
+                # directly instead of 301-redirecting through the base handle.
+                # Falls back to the base handle for products with no handle
+                # translation (e.g. some products keep the English slug in every
+                # locale).
+                link_handle = tr.get("handle") or handle
             image = (node.get("featuredImage") or {}).get("url", "") or ""
@@ -553,7 +556,7 @@
                 rows.append(ProductRow(
                     handle=handle,
                     id=sku,
                     title=title,
                     description=description,
-                    link=f"https://{STORE_DOMAIN_PUBLIC}{link_prefix}/products/{quote(handle)}?variant_sku={quote(sku)}",
+                    link=f"https://{STORE_DOMAIN_PUBLIC}{link_prefix}/products/{quote(link_handle)}?variant_sku={quote(sku)}",
                     image_link=image,
```

`handle` itself stays untouched everywhere else (`item_group_id`, the `handle=` field)
so cross-locale variant grouping doesn't change — only the URL used in `link` does.

### What I didn't do
I have no evidence for what the brief's specific "9 SKUs" are (no export was attached
to this conversation, and nothing matching that diagnosis exists in the repo) — I'm not
guessing at a list to match that number. The fix above is scoped to the actual bug I
could verify live, which is broader and different in kind from what was described.

### Proposed action
Approve the diff above, then re-run `--source shopify-api --market de --dry-run` (and
`be-fr`/`be-nl`) to confirm accepted-row counts don't drop, then a real run + spot-check
the sample table above resolves with no redirect. No file has been edited yet.
