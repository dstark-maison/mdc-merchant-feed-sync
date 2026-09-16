# Dimensions source check, Boomba exemption-flag prep, Coco & Cici sourcing scope

**No Shopify writes made.** Follows
`2026-09-10_coco-cici-boomba-gap-investigation.md`.

## 1. Orderchamp import-list CSV -- could not download, switched to direct sampling

The "Download CSV" button on `/v1/ds/import-list` was clicked twice (once
before this session and once during it, with your approval). Both attempts
produced no file in the Downloads folder after a 5+ second wait, and the
button's visibility was inconsistent between page loads (present in the
accessibility tree, absent from the rendered screenshot at some viewport
widths) -- looks like a flaky/responsive-layout bug on Orderchamp's side,
not a permissions issue on ours. Per standing guidance on not looping on a
failing browser action, I stopped after 2 attempts and fell back to
directly sampling individual Orderchamp listing pages instead (same method
as the prior investigation report) -- reliable, just slower per-product.

**A real, unplanned finding from this step: Casilin is not actually synced
through Orderchamp at all.** `/v1/ds/brands` lists 4 connected storefronts
-- MoST Blankets (Approved), Boomba Bamboo (Approved), Coco & Cici
(Approved), and **Casilin (Unpublished)**. The import list itself
("Showing 199 products", searched and paged through) contains **zero**
Casilin rows -- only MoST/Boomba/Coco & Cici products appear. Casilin's 32
active products in Shopify (per the Sep 10 audit) must come from a
different source entirely -- not this Orderchamp connection, despite the
original audit brief's assumption that Orderchamp supplies "sku/barcode for
Boomba Bamboo, Casilin, MoST Blankets, Coco & Cici." **This is a correction
to that assumption**, not just a data point -- worth tracking down
separately (a different app, a one-time CSV import, or manual entry) if
Casilin's own data gaps ever need root-causing the way this session did for
Boomba/Coco & Cici.

**Sampled coverage (not exhaustive -- CSV would have been exhaustive, this
is spot-checked per product line):**

| Vendor | Orderchamp source? | Sampled listings | L/W/(H) on Orderchamp? | Weight on Orderchamp? |
|---|---|---|---|---|
| Boomba Bamboo | Yes | Pillowcase (30x20x5cm), Bedding Set (40x30x40cm carton) | Yes, both | Yes (pillowcase, 400g); no (bedding set) |
| MoST Blankets | Yes | Wool bouclé throw PARIS dark red | Yes (200x140cm, no height -- flat item) | Yes (1,050g) |
| Coco & Cici | Yes | merino wool blanket, Bamboo pillowcase hazel | **No, neither** | **No, neither** |
| Casilin | **No -- not in Orderchamp** | n/a | n/a | n/a |

**Verdict:** the dimensions-mapping gap (Orderchamp has the data, Shopify's
`maison_seo.*` metafields don't) looks real and recoverable for **Boomba
(53 products) and MoST (60 products)** -- 113 of the audit's 182-product
dimension gap. It does not help Coco & Cici (39 products -- confirmed no
source data, see Part 3) or Casilin (32 products -- wrong pipeline
entirely, out of scope for an Orderchamp-side fix). Recovering the
Boomba/MoST 113 would require either getting the CSV working (retry later,
or ask Orderchamp support) or scripting a scrape of each product's
Orderchamp listing page -- **no writes proposed yet, this needs its own
follow-up pass** since it's ~113 products' worth of L/W/H (and occasionally
weight) to pull and map, distinct from the two approved tasks below.

## 2. Boomba Bedding Sets -- GTIN exemption flag, ready for your go-ahead

Re-fetched live: all 18 products, all with `customProduct` metafield
currently unset (null == false), confirming nothing has changed since the
investigation.

| # | Handle | Title | Product ID |
|---|---|---|---|
| 1 | `1-person-bamboo-bedding-set-coffee-brown-400tc` | 1-Person Bamboo Bedding Set Coffee Brown 400TC | `10795067113805` |
| 2 | `1-person-bamboo-bedding-set-coco-white-400tc` | 1-Person Bamboo Bedding Set Coco White 400TC | `10795067179341` |
| 3 | `1-person-bamboo-bedding-set-cuddle-pink-400tc` | 1-Person Bamboo Bedding Set Cuddle Pink 400TC | `10795070062925` |
| 4 | `2-person-bamboo-bedding-set-deep-moss-400tc` | 2-Person Bamboo Bedding Set Deep Moss 400TC | `10795070193997` |
| 5 | `2-person-bamboo-bedding-set-coffee-brown-400tc` | 2-Person Bamboo Bedding Set Coffee Brown 400TC | `10795070226765` |
| 6 | `1-person-bamboo-bedding-set-lavender-mist-400tc` | 1-Person Bamboo Bedding Set Lavender Mist 400TC | `10795070488909` |
| 7 | `1-person-bamboo-bedding-set-sky-blue-400tc` | 1-Person Bamboo Bedding Set Sky Blue 400TC | `10795071504717` |
| 8 | `1-person-bamboo-bedding-set-soft-taupe-400tc` | 1-Person Bamboo Bedding Set Soft Taupe 400TC | `10795071537485` |
| 9 | `1-person-bamboo-bedding-set-space-blue-400tc` | 1-Person Bamboo Bedding Set Space Blue 400TC | `10795071603021` |
| 10 | `1-person-bamboo-bedding-set-deep-moss-400tc` | 1-Person Bamboo Bedding Set Deep Moss 400TC | `10795071668557` |
| 11 | `2-person-bamboo-bedding-set-sky-blue-400tc` | 2-Person Bamboo Bedding Set Sky Blue 400TC | `10795071701325` |
| 12 | `2-person-bamboo-bedding-set-cuddle-pink-400tc` | 2-Person Bamboo Bedding Set Cuddle Pink 400TC | `10795071766861` |
| 13 | `2-person-bamboo-bedding-set-sage-green-400tc` | 2-Person Bamboo Bedding Set Sage Green 400TC | `10795071799629` |
| 14 | `2-person-bamboo-bedding-set-lavender-mist-400tc` | 2-Person Bamboo Bedding Set Lavender Mist 400TC | `10795071865165` |
| 15 | `2-person-bamboo-bedding-set-space-blue-400tc` | 2-Person Bamboo Bedding Set Space Blue 400TC | `10795071930701` |
| 16 | `2-person-bamboo-bedding-set-soft-taupe-400tc` | 2-Person Bamboo Bedding Set Soft Taupe 400TC | `10795071963469` |
| 17 | `white-bamboo-bedding-set-2-person-400tc-tanboocel-coco-white` | 2-Person Bamboo Bedding Set Coco White 400TC | `10795072029005` |
| 18 | `light-green-bamboo-bedding-set-1-person-400tc-tanboocel-sage-green` | 1-Person Bamboo Bedding Set Sage Green 400TC | `10795072061773` |

**Proposed diff (per product):**
```
mm-google-shopping.custom_product: (unset) -> "true"
```
Mutation (would run once per product, or batched via `metafieldsSet` with
up to 25 metafields per call):
```graphql
mutation {
  metafieldsSet(metafields: [
    { ownerId: "gid://shopify/Product/10795067113805", namespace: "mm-google-shopping", key: "custom_product", type: "boolean", value: "true" }
    # ...repeated for all 18 product IDs above
  ]) {
    metafields { id value }
    userErrors { field message }
  }
}
```
**Not pushed.** Reply to confirm and I'll run it (batched, 2 calls of 9
covers the 25-per-call limit comfortably) and re-fetch to confirm all 18
show `value: "true"` afterward.

## 3. Coco & Cici -- public site checked, confirmed dead end; scoped estimate

Found their retail site: **coco-cici.com**. Checked the exact product that
maps to one of MDC's 39 SKUs (`Tencel Twill Duvet Cover White` ->
`TENCEL™ Twill Dekbedovertrek Wit`,
coco-cici.com/products/tencel%E2%84%A2-twill-dekbedovertrek-wit) and its
parent collection page. **No specifications section, no weight, no
dimensions anywhere on the product page** -- only size/price variant rows
(140x200, 140x220, 200x200, 200x220, 240x220, 260x220, each + matching
pillowcases). Same absence pattern as Orderchamp. **Confirmed: this is a
manual-sourcing job, no shortcut available from data Coco & Cici publishes
anywhere.**

### Scoped estimate -- correcting the "well under 39" assumption

Pulled all 39 Coco & Cici products' variants and sizes live. The reduction
from color doesn't get you under 39 the way the brief hoped, because
**weight and package size are driven by SIZE, not color** -- and each
product carries 4-10 size variants. Breaking down what's actually there:

- **18 Duvet Cover products** across 3 fabric lines (Tencel Twill, Tencel
  Sateen, Bamboo/Eco Bamboo), each offered in the same 6 standard sizes
  (140x200+1pc ... 260x220+2pc) across ~16 colorways -- **102 SKUs total**.
- **18 Pillowcase products** across the same 3 fabric lines, up to 10
  distinct sizes (40x80 through 80x80, plus "without valance" variants that
  are genuinely different cut sizes, not just a label) -- **108 SKUs
  total**.
- **2 Duvet (insert) products** -- Four Seasons and High Summer fill types,
  7 and 6 sizes respectively -- **13 SKUs total**, no color variation.
- **1 Throw** -- single size, **1 SKU**.

Naive "distinct size x material combos" (ignoring color) comes to roughly
**18 (covers) + ~27 (pillowcases) + 13 (duvets) + 1 (throw) ~= 59** --
*more* than the 39-product count, not less, because size multiplies within
every product.

**Better approach than measuring every combo -- weight scales with area for
a uniform fabric, so this doesn't need 59 manual measurements:**
- **Weight**: weigh ONE reference size per fabric line (3 duvet-cover
  lines + 3 pillowcase lines + 2 duvet-insert fill types = **8 physical
  weighings**), derive that fabric's g/m^2 (or g/m^2-equivalent fill
  density for the inserts), then compute every other size's weight
  arithmetically from the size already encoded in each SKU's `Size` option
  (e.g., 200x220cm = 4.4m^2 x g/m^2). This is standard practice for
  uniform woven textiles and doesn't require weighing all 224 variants.
- **Package dimensions**: soft-folded textiles (duvet covers, pillowcases)
  typically ship in a small number of standard package-size tiers rather
  than scaling continuously with sheet size -- realistically **2-3
  package-size tiers to physically measure** (e.g., single-size vs.
  double/king-size folded package), not one measurement per SKU.

**Net realistic physical-sourcing scope: ~8 reference weighings + 2-3
package-size measurements**, not 39 and not 59 -- assuming this
scale-by-area approach is acceptable for eBay/Amazon/Otto's shipping-calc
purposes (it should be; declared package weight/dims for flat textiles are
rarely audited to the gram). This is a much smaller, well-defined task
someone could actually do in an afternoon. No writes proposed -- this needs
someone to do the physical measuring first.

## Summary of what needs your decision

1. **Boomba exemption flag (18 products)** -- diff shown above, ready to
   push on your confirmation.
2. **Boomba/MoST dimensions recovery (113 products)** -- real, recoverable
   source data confirmed on Orderchamp; needs a follow-up pass (CSV retry
   or scripted scrape) before any metafield writes -- not started.
3. **Coco & Cici (39 products)** -- confirmed manual-sourcing job; scoped
   to ~8 weighings + 2-3 package measurements if the scale-by-area approach
   is acceptable. No writes possible until those measurements exist.
4. **Casilin (32 products)** -- newly discovered to be outside Orderchamp
   entirely; its dimension/weight/GTIN gaps need their own source
   investigation, separate from everything above.
