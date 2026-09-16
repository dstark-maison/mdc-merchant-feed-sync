# Coco & Cici weight/dimensions + Boomba Bedding Sets GTIN — root-cause investigation

**Read-only. No Shopify writes made.** Follows up on
`2026-09-10_channable-readiness-audit.md`. Investigated by comparing live
Shopify data (already pulled in the audit) against Orderchamp's own
dropshipping dashboard (integrations.orderchamp.com / orderchamp.com, logged
in as the connected Shopify store), since Orderchamp is a fully external
managed app -- there is no sync-mapping code in this repo to inspect; the
only way to check "does the source data exist" is Orderchamp's own UI.

## Orderchamp import settings (checked first, applies to both parts)

Settings > Product characteristics (orderchamp.com/v1/ds/settings) has
**Dimensions, Weight, EAN, SKU, Brand Name, Colors, Materials, Made in,
Allergens, and GPSR all already checked/enabled** for this store's
connection -- only "Ingredients" is off. So the import settings are not the
problem; nothing is toggled off that should be on.

But Orderchamp's own copy on that page matters: *"These additional fields
are not mapped by most systems and will therefore appear below the product
description in your store."* Confirmed by comparing against live Shopify
data:
- **Weight** and **EAN** (barcode) genuinely DO sync into Shopify's native
  fields (`inventoryItem.measurement.weight`, variant `barcode`) when the
  brand supplies them on Orderchamp -- verified matching values (see below).
- **Dimensions (Length/Width/Height)** do NOT reach Shopify's native fields
  or the `maison_seo.height/width/length` metafields at all, even when
  Orderchamp's own listing page clearly has them. They only ever land as
  free text below the product description -- and this store's live
  `descriptionHtml` for every product checked is clean, custom-written
  marketing copy with zero trace of appended spec text. The content
  pipeline that writes these descriptions (see `mdc-blog-pipeline` /
  `mdc-publish-content-meta`) overwrites whatever Orderchamp appended,
  before anyone captures it.

**This is the real, catalog-wide root cause of the dimensions gap** (182
products across all 4 Orderchamp vendors, per the audit) -- not a broken
toggle, but a structural mismatch: Orderchamp's only delivery mechanism for
dimensions is unstructured description text, and this store's content
pipeline discards that text. It's separate from, and doesn't explain, the
weight/GTIN gaps below, which are source-data gaps specific to those SKUs.

## Part 1 -- Coco & Cici: 100% weight + dimension gap (39 products)

**Root cause: missing at the source, not a mapping bug.** Checked two live
Orderchamp listings for Coco & Cici products already in this store's
catalog:

- `merino wool blanket` (SKU `731602202`) -- Product specifications block
  shows only **Brand Location, SKU, EAN** (`7423614454497`). No Length,
  Width, Height, or Weight fields at all.
- `Bamboo pillowcase hazel` (SKU `B260705`) -- same pattern: **Brand
  Location, SKU, EAN** (`8721184400432`) only. No dimensions, no weight.

Compare to a Boomba Bamboo listing from the same marketplace (below) which
DOES show Length/Width/Height/Weight in the same "Product specifications"
block layout -- so this isn't a rendering quirk, it's genuinely absent for
Coco & Cici. EAN is present and correctly flowing into Shopify's `barcode`
field for this vendor (matches the audit's "0 GTIN gaps" finding for Coco &
Cici) -- confirming Orderchamp's sync mechanism itself works fine when the
brand actually supplies a field.

**Verdict: manual sourcing job, per your branch 3.** Orderchamp/Coco & Cici
never populated weight or dimensions for this brand's catalog. No amount of
mapping-fix work on our side will produce this data -- it isn't there to
map. Scope: 39 products (~100+ variants). Options, in order of effort:
1. Ask Coco & Cici directly (via Orderchamp's "Chat with brand" feature,
   visible on every listing page) whether they have weight/dimension specs
   they haven't published to Orderchamp -- cheapest if they say yes.
   Note the Content Pipeline caveat above: even if they populate them on
   Orderchamp, the dimensions still won't land in `maison_seo.*` — the
   description-overwrite problem would need fixing too.
2. Pull specs from Coco & Cici's own manufacturer/DTC website product pages
   (not Orderchamp) and hand-enter into Shopify's native weight field +
   `maison_seo.height/width/length` metafields.
3. Do not use `mm-google-shopping.custom_product` (the "no identifier"
   exemption) here -- that flag is about GTIN/MPN, not weight/dimensions,
   and doesn't apply to this gap at all.

No writes proposed for this part yet -- no verified values exist to write.

## Part 2 -- Boomba Bamboo: GTIN gap in 18 "Bedding Sets" SKUs

**Root cause: genuinely no GTIN at the source for this specific bundle SKU
family -- confirmed, not assumed.** Checked Orderchamp's own listing for
`Light Green Bamboo Bedding Set 1-person - 400TC Tanboocel - Sage Green`
(the exact product behind Shopify handle
`light-green-bamboo-bedding-set-1-person-400tc-tanboocel-sage-green`,
Shopify SKU `3333338014050`):

- Product specifications: **Length 40cm, Width 30cm, Height 40cm** (this
  is carton/package size -- a set of 2 variants, M and L), **Made in
  China**, **SKU 3333338014050**. **No EAN field is shown at all** -- the
  first time we've seen a Boomba listing without one.
- The repeating-digit SKU pattern seen across all 18 Bedding Sets in
  Shopify (`9999998014050`, `5555558014050`, `7777776020060`, etc.) is
  Orderchamp's own internal listing SKU, not a barcode -- confirmed
  directly on the source page, not inferred.
- Compared against `White bamboo Pillowcases Premium ... Coco White`
  (a normal, well-covered Boomba SKU family): that listing shows **Length
  30cm, Width 20cm, Height 5cm, Weight 400g, SKU 8720648154188** -- and
  8720648154188 is a real GS1-13 barcode, which does match what's live in
  Shopify's `barcode` field for that product. Same brand, same Orderchamp
  UI, EAN present there and absent on the Bedding Sets -- this is a
  per-SKU-family gap specific to bundles, exactly matching the audit's
  hypothesis (35 individually-sold products ~97% clean, 18 bundle
  products 0% clean).

**Why bundles specifically:** a "Bedding Set" is Boomba's own pre-packaged
combination of items (duvet cover + fitted sheet + pillowcases) that
individually DO have GTINs (confirmed: the Duvet Cover, Fitted Sheet, and
Pillowcase listings all show EAN). Boomba/Orderchamp never assigned a
bundle-level GTIN to the combo SKU -- a common real-world gap for
private-bundle products, not a data-entry oversight.

**Verdict: exemption flag is the correct fix here, per your branch-3
guidance -- checked first, and there genuinely is no bundle GTIN.** These
18 products should get `mm-google-shopping.custom_product = true` (the
"no unique product identifier" declaration), which is honest here (no
GTIN exists for the bundle) rather than a workaround for missing sourcing
effort. This is a **product-level write** across 18 specific SKUs -- listed
below -- not a whole-vendor change.

**Bonus, same dimensions issue as Part 1:** the Bedding Sets DO have real
carton dimensions on Orderchamp (40x30x40cm for this one) that aren't
reaching `maison_seo.height/width/length` -- same structural
description-overwrite gap, not specific to this SKU family.

## Recommended next steps (need your go-ahead before any write)

1. **Boomba Bedding Sets (18 products) -- propose setting
   `mm-google-shopping.custom_product = true`.** I have not pushed this.
   Confirm and I'll list the exact 18 product IDs, show the mutation, and
   push only after you approve.
2. **Coco & Cici (39 products) -- no fix available from data we control.**
   Recommend: you (or I, with your go-ahead) message Coco & Cici via
   Orderchamp's in-app chat asking for weight/dimension specs; in parallel
   I can check their public site for spec sheets if you want that done now.
3. **Dimensions mapping gap (all 4 Orderchamp vendors, ~182 products) is
   the actual highest-leverage fix**, bigger than either part above: for
   every Orderchamp product where the brand DOES supply L/W/H (confirmed
   for at least several Boomba SKU families, likely more), that data sits
   on Orderchamp's own listing pages and could be captured with a one-time
   scrape/export (the Import List page has a "Download CSV" button I did
   not click -- downloading files needs your explicit OK per standing
   policy) and written into `maison_seo.height/width/length` directly,
   bypassing the broken description-append path entirely. This wouldn't
   fix Coco & Cici (data doesn't exist there) but would fix a meaningful
   chunk of Boomba/MoST/Casilin's 182-product dimension gap. Want me to
   pursue this as a separate follow-up?

No Shopify writes were made in this investigation. No files outside this
report were touched.
