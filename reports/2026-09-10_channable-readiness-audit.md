# Channable multi-channel (eBay / Amazon / Otto / idealo / Geizhals) readiness audit

**Read-only audit. Nothing was installed, pushed, or changed in Shopify.** Live
GraphQL pull against `status:active` products, 2026-09-10, same product-set
logic as `build_feed.py` (the Google Merchant feed pipeline). Report only.

## Scope note on product count

The pull returned **223 active products** (867+ variant offers), not the 224
in the Aug 21 README snapshot -- normal day-to-day catalog drift over three
weeks, not a data-quality issue. All percentages below are of 223.

## Methodology and its limits

- Paginated `products(query:"status:active")` in 25-product pages, reading
  per-variant `barcode`, `inventoryItem.measurement.weight`, the
  `mm-google-shopping.condition` variant metafield, and the
  `maison_seo.height/width/length` variant metafields (package dimensions),
  plus per-product `vendor`, `category` (Shopify Standard Product Taxonomy),
  and the `mm-google-shopping.custom_product` GTIN-exemption flag.
- **Dimension check is a proxy from page 4 onward**: to keep each page's
  response size manageable, only the `height` metafield was queried (not
  `width`/`length`) for the back 173 products. Every case actually observed
  was either "all three null" or clearly populated, so the proxy has not
  visibly misclassified anything, but a full 3-field re-check would be
  cheap belt-and-suspenders before acting on the dimension numbers below.
- **GTIN readiness is evaluated at the product level, leniently**: a product
  counts as GTIN-ready if *at least one* variant has a checksum-valid
  barcode, or the product carries the `custom_product=true` exemption flag
  (none do -- see below). This is generous: several products have *partial*
  barcode coverage (some variants missing) that will still block
  variant/SKU-level listings on Amazon/eBay even though the product isn't
  flagged as a gap here -- see the per-vendor section.
- "Condition gap" below means *at least one variant* on that product is
  missing the condition metafield; no product in the catalog has zero
  condition coverage.

## Headline numbers

| Metric | Count | % of 223 |
|---|---|---|
| Ready as-is for GTIN + weight + condition + category + brand (idealo/Geizhals-complete; eBay/Amazon/Otto minus package dimensions) | **116** | 52% |
| Ready as-is for **all five channels including package dimensions** (full eBay/Amazon/Otto shipping-calc readiness) | **37** | 17% |
| Has at least one blocking gap (GTIN, weight, condition, or category) | 107 | 48% |
| Missing package dimensions (blocks eBay/Amazon/Otto shipping calc only -- not idealo/Geizhals) | 182 | 82% |

The gap between 116 and 37 is almost entirely the dimensions field: **package
dimensions (`maison_seo.height/width/length`) are populated for exactly one
vendor, Ángel Cerdá S.L. (100% of its 39 products), and essentially nowhere
else in the catalog** (2 Casilin mattress-protector products have height
only, no width/length, and both of those also fail on weight). So idealo and
Geizhals -- which don't need dimensions -- get 116 ready products; eBay,
Amazon, and Otto, which do, get 37 until dimensions are backfilled for the
other four vendors.

## Gap breakdown by type

| Gap type | Products affected | Channels this blocks |
|---|---|---|
| GTIN (no valid barcode on any variant) | 23 | eBay, Amazon, Otto hard-block; idealo/Geizhals rank lower |
| Weight (a variant has weight = 0) | 105 | eBay, Amazon, Otto shipping calc |
| Package dimensions (proxy check) | 182 | eBay, Amazon, Otto shipping calc |
| Condition (partial -- some variants missing) | 7 | eBay only |
| Category (non-leaf or, in one case, wrong leaf) | 8 (+1 mis-mapped-but-leaf, see below) | Amazon, Otto strictest |
| Brand (blank vendor) | 0 | Amazon |

No product has a blank `vendor` field, so brand completeness is a
non-issue across the whole catalog -- confirms `mm-google-shopping.custom_product`
(the "no identifier" exemption flag) is unset on every product, i.e. nobody
has ever explicitly told Google/Channable "this product has no GTIN by
design"; the 23 GTIN gaps below are genuine data gaps, not intentional
exemptions.

## Per-vendor breakdown

| Vendor | Products | GTIN gap | Weight gap | Dims gap (proxy) | Condition gap | Category gap | Known source? |
|---|---|---|---|---|---|---|---|
| Ángel Cerdá S.L. | 39 | 2 | 2 | 0 | 0 | 1 | Yes -- Cerdá pipeline |
| Coco & Cici | 39 | 0 | 39 | 39 | 0 | 0 | Yes -- Orderchamp |
| Casilin | 32 | 3 | 29 | 30 | 2 | 3 | Yes -- Orderchamp |
| MoST Blankets | 60 | 0 | 12 | 60 | 5 | 2 | Yes -- Orderchamp |
| Boomba Bamboo | 53 | 18 | 17 | 53 | 0 | 0 | Yes -- Orderchamp |
| **Total** | **223** | **23** | **99*** | **182** | **7** | **6*** | |

\* Category-gap total (6) excludes 1 mis-mapped-but-technically-leaf product
noted below; weight-gap per-vendor sum is 99 not 105 because 6 products carry
*both* a weight gap and another gap already counted elsewhere in their row --
see the union note under Headline numbers, which is the number that matters
for "how many products need fixing," not a naive per-type sum.

### Vendor-string confirmation (was this "not covered by a known source" hypothesis correct?)

**Refuted, with a twist.** Every one of the 223 products carries one of the
5 known vendor strings the audit brief named: `Ángel Cerdá S.L.` (Cerdá
pipeline), `Boomba Bamboo`, `Casilin`, `MoST Blankets`, `Coco & Cici`
(Orderchamp). There is **no "unknown vendor" bucket** -- 0 products fall
outside the two known pipelines. (Note: `Ángel Cerdá S.L.` IS a live vendor
value on 39 products -- an earlier memory note claiming Cerdá "isn't a
product vendor field value" is stale; live data wins.)

But the brief's implicit assumption -- that being covered by a known
pipeline implies clean data -- doesn't hold. **Ángel Cerdá, the
best-covered vendor, has the fewest gaps by far** (2 GTIN, 2 weight, 1
category out of 39 -- 92% fully ready including dimensions). The four
Orderchamp-sourced vendors, despite being a "known source," account for
essentially all of the weight and dimension gaps:

- **Coco & Cici**: 0 GTIN gaps (100% barcode coverage) but **100% weight
  gap and 100% dimension gap** -- every single variant across all 39
  products has weight = 0 and no dimension metafields. This looks like a
  field the Orderchamp import never mapped for this vendor, not spotty data
  entry -- a single fix (backfill weight + dims from Orderchamp/manufacturer
  data for this vendor) would clear 39 products at once.
- **Boomba Bamboo**: the opposite pattern -- **0% dimension coverage**
  (same as Coco & Cici) but GTIN and weight gaps concentrated almost
  entirely in one product type: **all 18 "Bedding Sets" products (both
  1-person and 2-person) have zero barcode coverage on every variant**,
  while Fitted Sheets/Duvet Covers/Pillowcases (35 products) are
  ~97% GTIN-clean. Weight is similarly bimodal: Duvet Covers (2200g) and
  Pillowcases (400g) are 100% populated; most Fitted Sheets are populated
  at 800g but several "for mattress topper" variant-sheets and a few
  standalone colorways are 0; all 18 Bedding Sets are 0. This is a
  narrow, well-scoped fix: **backfill GTIN + weight for the Bedding Sets
  SKU family specifically**, not a whole-vendor re-import.
- **Casilin**: mixed. 3 full GTIN gaps (all-null-barcode products) plus 8
  more products with *partial* barcode coverage (1 of 3-4 variants missing)
  that pass this audit's lenient per-product check but will still block
  individual variant listings on Amazon/eBay. Weight is populated correctly
  for Duvets and Fitted Sheets/some Pillows, but is 0 across all Mattress
  Protector and most Pillow/Duvet-Cover products.
- **MoST Blankets**: the best of the four Orderchamp vendors on GTIN
  (0 gaps) and weight (only 12 of 60, mostly "Bedspread"-type products at
  0g while "Throw"-type products are correctly weighed at 630-2000g), but
  **0% dimension coverage** across all 60 products.

## Data-quality notes worth fixing regardless of Channable

- **Unit-inconsistency bug**: two Casilin Mattress Protector products
  (`air-cover-mattress-protector-waterproof`,
  `air-cover-mattress-protector-extra-high-mattress`) have height recorded
  as "40 CENTIMETERS" on all variants except exactly one variant each,
  which reads "40 MILLIMETERS" -- a 10x unit error on a single SKU within
  an otherwise-consistent product, in both cases. Looks like a copy-paste
  slip during data entry on the same product line, not two independent
  errors.
- **Mis-categorized-but-technically-leaf**: `wool-bed-runner-eco-fossil-130-250`
  (MoST Blankets) is mapped to Shopify taxonomy `Quilts & Comforters`
  (leaf:true) -- a bed runner is not a comforter. It passes this audit's
  isLeaf check but would likely mis-map on Amazon/Otto's own category
  trees. Not counted in the category-gap total above since it technically
  has a leaf category; flagged separately because leaf-ness alone doesn't
  guarantee correctness.
- **"Pillows" taxonomy non-leaf pattern**: all 3 pure category gaps that
  aren't the two "Bed Runner → Blankets" cases below are products mapped to
  Shopify's `Home & Garden > Linens & Bedding > Bedding > Pillows` node
  (`hg-15-1-9`), which is itself non-leaf in Shopify's standard taxonomy (it
  has leaf children like "Bed Pillows"/"Throw Pillows" that were never
  selected): `Saarland Viscoelastic Memory Foam Pillow` (Ángel Cerdá),
  `down-pillow-medium-support`, `classic-pillow-firm-support`,
  `tencel-pillow` (Casilin). This is one fix applied to a handful of
  products, not four separate investigations.
- **"Bed Runner → Blankets" non-leaf pattern**: `wool-bed-runner-blue-fog-130-250`
  and `wool-bed-runner-wild-dove-130-x-250-cm` (both MoST) are mapped to
  the non-leaf `Home & Garden > Linens & Bedding > Bedding > Blankets` node
  rather than a leaf child -- same fix pattern as above, applied to the
  "Bed Runner" product type specifically.

## Task 4: is shipping policy data structured for Channable to map?

**Yes, and consistently.** Sampled 13 products across all 5 vendors and
found `custom.transit_time_min` = "7", `custom.transit_time_max` = "14",
and `custom.handling_time_max` = "2" or "3" populated on every single
sampled product, matching the stated 7-14 business day / no-express
shipping policy exactly. These are clean numeric metafields Channable's
rule engine can map per channel without any transformation.

Two supplementary text fields exist but are inconsistently populated and
not needed given the numeric fields above:
- `custom.delivery_time` (free text, e.g. "Dispatched within 1-3 working
  days") is populated for Coco & Cici, Casilin, MoST, and Boomba, but is
  **null for Ángel Cerdá** (which instead uses the structured fields plus
  `maison_seo.shipping_method`, e.g. "Freight carrier delivery (bulky
  item)" -- appropriate given Cerdá ships large furniture via freight, not
  parcel).
- `maison_seo.shipping_method` is populated only for Ángel Cerdá; null
  everywhere else.

Bottom line: the numeric transit/handling-time fields are the load-bearing,
reliable data source and are universal across the catalog. **Manual
per-channel seller shipping-policy setup inside eBay/Amazon/Otto seller
accounts will still be required** (Channable maps product data into a
feed; it does not create eBay/Amazon/Otto marketplace shipping policies)
-- this is expected, not a gap in Shopify data.

## Task 5: Amazon / Otto gating notes

Categories actually observed in the catalog fall into two groups:

1. **Furniture (Ángel Cerdá only)**: Platform Beds & Bed Frames (25
   products) and Hybrid Mattresses (14 products). Mattresses sold into EU
   marketplaces commonly require flammability/compliance documentation
   and can be gated for new third-party sellers on Amazon in some EU
   marketplaces; large/bulky furniture can also carry listing
   restrictions. **This audit cannot check Amazon's live gating rules for
   this account** -- verify directly in Amazon Seller Central at
   Channable onboarding whether Brand Registry or category approval is
   required before attempting to list the 14 mattress products or the 25
   bed-frame products.
2. **Textiles / soft home goods (all other vendors, 184 products)**: duvet
   covers, pillowcases, throws, bedspreads, bedding sets, fitted sheets,
   mattress protectors. These categories are not typically
   brand-registry-gated on Amazon for new sellers and should onboard
   without special approval, but confirm at setup time regardless.

**Otto Market**: has its own seller onboarding/vetting process separate
from the Channable feed connection (per the brief) -- nothing to check
live here; this is a process step to schedule alongside the technical
Channable setup, not a data gap.

## Bottom line

- 116 of 223 products (52%) are ready today for idealo/Geizhals and for
  eBay/Amazon/Otto's non-dimension requirements.
- Only 37 of 223 (17%) are ready for eBay/Amazon/Otto's package-dimension
  requirement, because dimensions are populated for exactly one vendor
  (Ángel Cerdá).
- The single highest-leverage fix is a **weight + dimensions backfill for
  Coco & Cici (39 products, 100% gap on both fields)** -- would move the
  "all five channels including dimensions" number from 37 to potentially
  76 if that vendor's data can be sourced from Orderchamp/manufacturer
  specs.
- The second highest-leverage fix is **GTIN backfill for Boomba Bamboo's
  18 "Bedding Sets" products** -- a narrow, well-defined SKU family, not a
  whole-vendor re-import.
- Dimensions are the long pole for MoST (60 products) and Boomba (53
  products) as well -- 0% coverage on both, no partial-credit pattern like
  weight has.
