# Casilin removal verification (32 products) -- read-only check

Casilin's 32 products were confirmed genuinely deleted from Shopify (not
archived/unpublished) shortly before this check: `vendor:'Casilin'` returns
zero results at any status, and `productByHandle` on former Casilin handles
returns `null`. This report verifies whether the removal was clean.
**Nothing was fixed -- this is observe-only, per instructions.**

**Tooling note:** plain `curl` against this domain returns 404 for every
URL tested, including ones independently confirmed live via WebFetch (e.g.
a genuinely-live product came back 404 via curl but rendered its real
German page fine via WebFetch). All checks below used WebFetch instead of
curl for that reason.

## Task 1 -- Old product URLs: FAIL (all 32 hard-404, zero redirects)

All 32 former Casilin handles were checked at
`https://www.maisondecocon.com/products/<handle>` via WebFetch. **All 32
return a clean HTTP 404 with no redirect** -- Shopify's own removal
process (or whatever removed these) did not create any URL redirects.
None are still live (no false-positive "still somehow resolving" cases).

Every one of these 32 URLs is a dead end with no redirect -- this is the
concrete SEO/UX cost the brief asked about (lost link equity on any
external backlinks, broken bookmarks, and broken links from anywhere that
isn't caught by Tasks 2/3/5 below):

```
air-cover-mattress-protector-extra-high-mattress
air-cover-mattress-protector-waterproof
air-cover-pillow-protector
classic-pillow-firm-support
classic-pillow-medium-support
cotton-muslin-duvet-cover-beige
crinkly-cotton-muslin-ice-blue
crinkly-cotton-muslin-tetra-brick-red
crinkly-cotton-muslin-tetra-ecru
crinkly-cotton-muslin-tetra-light-grey
crinkly-cotton-muslin-tetra-misty-blue
crinkly-cotton-muslin-tetra-petrol-blue
crinkly-cotton-muslin-tetra-sage-green
crinkly-cotton-muslin-tetra-sahara-pink
crinkly-cotton-muslin-tetra-stripe-grey
down-pillow-medium-support
duvet-crown-goose-down-duvet
duvet-duke
duvet-prince-duo-4-seasons-down-comforter
duvet-satin-duo
duvet-satin-mono
duvet-summer-cotton-light
duvet-super-soft-duo
duvet-super-soft-light
duvet-super-soft-mono
eco-sfera-mattress-protector-waterproof
respira-pillow-protector-waterproof-white
royal-percale-fitted-sheet-beige
royal-percale-fitted-sheet-light-grey
tencel-pillow
tencel-satin-duvet-cover-set-lila
thermocomfort-frost-mattress-protector
```

**Verdict: FAIL on redirect hygiene** (pass on "nothing is orphaned/still
live", which is the more dangerous failure mode -- but the brief specifically
flagged hard-404-no-redirect as the thing that hurts SEO/UX, and that's
exactly what happened on all 32).

## Task 2 -- Internal references: PASS, clean

- Searched `maison-de-cocon-shopify-theme/`, `shopify-content/`,
  `mdc-blog-pipeline/`, `mdc-publish-content-meta/` (excluding
  `node_modules`/`.git`) for the literal string "Casilin" -- **zero
  matches**.
- Searched the same repos for each of the 32 handles individually as
  literal strings (covers hardcoded Liquid links, JSON template blocks,
  blog post bodies, FAQ content) -- **zero matches for any handle**.
- Queried all 6 live Shopify menus (`main-menu`, `footer`,
  `customer-account-main-menu`, `footer-support`, `footer-menu-legal`,
  `footer-about`) including nested sub-items -- every link is a category
  collection, a static page, the blog, or an external Trustpilot review
  link. **No menu item references Casilin, a Casilin product, or a
  Casilin-specific collection.**

**Verdict: PASS.** No dangling hardcoded references anywhere in code or
navigation.

## Task 3 -- Sitemap: PASS (spot-checked)

`sitemap.xml` is an index with per-locale `sitemap_products_1.xml` entries
(DE default + `/en/`, `/nl/`, `/fr/`). Fetched the DE product sitemap (371
`<loc>` entries) and checked 6 handles spanning different product-name
patterns (mattress protector, crinkly muslin, satin duvet cover, "duvet
duke", tencel pillow, percale fitted sheet) -- **none appear**. Shopify
generates this file automatically from live product state, so a 6-handle
spot check across varied patterns is sufficient confidence; the removal
correctly propagated to the sitemap.

**Verdict: PASS.**

## Task 4 -- Merchant feed pipeline: **FAIL -- live, currently-active bug**

This is the most consequential finding. Checked
`mdc-merchant-feed-sync/data/*.csv` (all 4 market feeds: DE, EN, BE-FR,
BE-NL) and confirmed against `git fetch origin` that the local repo is
fully in sync with `origin/master` -- **this is the same file content
Google Merchant Center is fetching right now** via the public
`raw.githubusercontent.com` URL, not a stale local-only copy.

- **All 4 feed files still contain 178 Casilin variant rows each**, with
  `link` values pointing at the 32 now-404ing URLs from Task 1.
- Last successful feed build: commit `25bdb97`, GitHub Actions run
  `34471306535`, **2026-09-10 11:26:32 UTC** -- before Casilin was
  removed.
- `gh run list` shows no run yet today (2026-09-11); the daily scheduled
  build has historically fired ~10:20-11:30 UTC (not exactly the 06:00 UTC
  the README describes), and as of this check (~08:46 UTC) that window
  hasn't arrived yet. This is **not a broken pipeline** -- the next
  scheduled run should pick up Casilin's absence automatically, since
  `load_products_from_shopify_api` only ever pulls `status:active`
  products and Casilin now has none.

**Verdict: FAIL right now, self-healing within ~1-3 hours** on the next
scheduled run, unless triggered manually sooner (see recommendations).
Until that run happens, Google Merchant Center is being served 178 offers
with dead links.

## Task 5 -- Collections: **FAIL -- "Bedding Accessories" is empty and still linked in nav**

Queried live collections matching accessory/protector/mattress terms:

| Collection | Handle | Rule | Live product count |
|---|---|---|---|
| Beds & Mattresses | `beds-mattresses` | manual/curated | 39 |
| Mattresses | `mattresses` | `TYPE = Mattresses` | 11 (unaffected -- Ángel Cerdá's mattress line, unrelated to Casilin's mattress *protectors*) |
| **Bedding Accessories** | **`bedding-accessories`** | **`TAG = bedding-accessory`** | **0** |

**"Bedding Accessories" is a smart collection (tag-rule) that now returns
zero products** -- almost certainly because Casilin's mattress-protector
and pillow-protector products (`air-cover-mattress-protector-*`,
`eco-sfera-mattress-protector-waterproof`,
`respira-pillow-protector-waterproof-white`,
`thermocomfort-frost-mattress-protector`) were tagged `bedding-accessory`
and were the only products carrying that tag.

**This collection is still linked from live navigation** -- confirmed in
Task 2's menu dump: it appears as **"Accessories"** in the main site menu
(top-level nav item) AND as **"Bedding Accessories"** in the footer menu.
Both currently lead a visitor to an empty collection page.

**Verdict: FAIL.** This is a real, currently-live broken navigation path,
not a hypothetical.

## Summary: 2 PASS, 3 FAIL

| Task | Verdict |
|---|---|
| 1. Old URLs | FAIL -- clean 404s (no orphans), but zero redirects on 32/32 |
| 2. Internal references | PASS |
| 3. Sitemap | PASS |
| 4. Merchant feed | FAIL -- live stale feed, self-healing in ~1-3h |
| 5. Collections | FAIL -- "Bedding Accessories" nav item leads to 0 products |

## If you want these fixed (not applied -- your call)

1. **Highest priority: empty "Accessories" / "Bedding Accessories" nav
   item.** Either remove it from `main-menu` and `footer` until it has
   real products again, or repoint it at a non-empty collection, or add
   replacement accessory products. This is the one actively visible to
   every site visitor right now.
2. **Merchant feed freshness**: either wait for the next scheduled run
   (expected ~10:20-11:30 UTC today based on the historical pattern) or
   manually trigger it now via `gh workflow run "Merchant feed daily
   build"` from `mdc-merchant-feed-sync/` to clear the 178 stale rows
   immediately instead of leaving dead links in front of Google Merchant
   Center for a few more hours.
3. **Redirects for the 32 dead URLs** (lower priority, since Task 2/3
   confirm nothing internal or in search indexes still points at them):
   add 301 redirects from each old handle to the closest matching
   collection (e.g. the mattress-protector/pillow-protector handles ->
   whatever collection replaces "Bedding Accessories", the duvet/pillow
   handles -> `/collections/duvets` or `/collections/pillows`) via
   Shopify Admin > Online Store > Navigation > URL Redirects, to recover
   some SEO value from any existing backlinks or indexed search results
   for these 32 URLs.

## Fixes applied 2026-09-11 -- all three items resolved

**1. Nav item unlinked.** `menuUpdate` on `main-menu` (removed "Accessories")
and `footer` (removed "Bedding Accessories"), both against the exact live
item lists pulled immediately before the edit. Zero `userErrors` on either
call. The `bedding-accessories` collection itself was left untouched in
the backend, per instruction -- only the two nav links were removed.

**2. Feed rebuild triggered manually.** Ran `gh workflow run "Merchant feed
daily build"` (confirmed exact name via `gh workflow list` first) instead
of waiting for the scheduled run. Run `34580695151` completed successfully
in 24s and committed `cf9c2d5` to `origin/master`. Verified row counts
directly against `origin/master` (the same content GMC fetches):

| File | Casilin rows before | Casilin rows after | Total rows after |
|---|---|---|---|
| `google_merchant_feed.csv` (DE) | 178 | **0** | 699 |
| `google_merchant_feed_en.csv` | 178 | **0** | 699 |
| `google_merchant_feed_be_fr.csv` | 178 | **0** | 699 |
| `google_merchant_feed_be_nl.csv` | 178 | **0** | 699 |

**3. All 32 redirects created**, batched into a single GraphQL mutation
(32 aliased `urlRedirectCreate` calls in one request) -- zero `userErrors`
across all 32:

| Target | Count | Handles |
|---|---|---|
| `/` (homepage -- no accessories collection exists to redirect into) | 6 | mattress/pillow protector handles |
| `/collections/duvet-covers` | 11 | crinkly-cotton-muslin-* + cotton-muslin-duvet-cover-beige + tencel-satin-duvet-cover-set-lila |
| `/collections/duvets` | 9 | duvet-satin-duo/mono, duvet-super-soft-*, duvet-summer-cotton-light, duvet-duke, duvet-prince-duo-4-seasons-down-comforter, duvet-crown-goose-down-duvet |
| `/collections/pillows` | 4 | classic-pillow-*, tencel-pillow, down-pillow-medium-support |
| `/collections/fitted-sheets` | 2 | royal-percale-fitted-sheet-* |

Classification for the duvet-cover vs. duvet-insert split was done by
reading each product's actual description from the last pre-removal feed
export, not just title pattern-matching -- e.g. "Duvet - Satin Duo" reads
as a cover-shaped name but its description ("This 4-season duvet combines
two duvets that connect with press studs") confirms it's an insert, routed
to `/collections/duvets` accordingly.

**Spot-checked 4 of the 32 post-fix** via WebFetch (curl is unreliable
against this domain, confirmed earlier in this report):
- `air-cover-mattress-protector-waterproof` -> homepage (confirmed: real
  homepage content, not a 404)
- `duvet-duke` -> `/collections/duvets` ("Bettdecken" page, 2 products)
- `royal-percale-fitted-sheet-beige` -> `/collections/fitted-sheets`
  ("Spannbettlaken" page, 17 products)
- `crinkly-cotton-muslin-tetra-ecru` -> `/collections/duvet-covers`
  ("Bettbezüge" page, 27 products)

All 4 redirect correctly. **All 5 original findings from this report are
now resolved**: Task 1 (dead URLs) now redirect instead of hard-404ing,
Task 4 (stale feed) is rebuilt and clean, Task 5 (broken nav) is unlinked.
Tasks 2 and 3 (internal references, sitemap) were already clean and
required no action.
