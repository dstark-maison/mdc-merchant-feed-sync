# Boomba exemption push, Casilin source trace, Boomba/MoST dimension scrape

Follows `2026-09-10_dimensions-source-check_boomba-exemption_coco-cici-sourcing.md`.

## 1. Boomba Bedding Sets GTIN exemption -- pushed and confirmed

Ran `metafieldsSet` for `mm-google-shopping.custom_product = true` (type
`boolean`) on all 18 Boomba Bedding Sets product IDs listed in the prior
report. `userErrors: []` on the mutation response -- all 18 wrote cleanly.
Re-fetched live immediately after: **all 18 handles now read back
`customProduct.value: "true"`**. No failures.

## 2. Casilin source trace -- conclusive, not inconclusive

**Finding: Casilin's 32 Shopify products WERE created by Orderchamp** --
this corrects the prior report's framing ("not Orderchamp-sourced at all"),
which was based only on the *current* import list and brand-connection
status. The product event log tells the fuller, historical story.

Checked `events` (Shopify's built-in product activity log) on 3 live
Casilin products (`crinkly-cotton-muslin-tetra-ecru`,
`tencel-satin-duvet-cover-set-lila`,
`crinkly-cotton-muslin-tetra-misty-blue`). All three show an identical
pattern:

1. `2026-08-03T13:41:39Z` -- **"Orderchamp created a new product"**
   (appTitle: `Orderchamp`)
2. `2026-08-03T13:41:40Z` -- **"Orderchamp changed product status from
   active to draft"** (same app, one second later -- Orderchamp itself
   drafted the product immediately after creating it)
3. `2026-08-06T17:00-17:08Z` -- a human (`appTitle: "Shopify Web"`,
   attributed to the logged-in account) published the product to Online
   Store / Google & YouTube / Inbox and flipped status back to active
4. `2026-09-08T13:13:43Z` -- a "Product was included on Shop" channel
   event, no app attribution

No events after the initial create/draft cycle are attributed to
Orderchamp -- **no automated Orderchamp update has touched these products
since 2026-08-03**. That's consistent with, not contradictory to, what
`/v1/ds/brands` shows today: Casilin's Orderchamp connection status is
**"Unpublished"**, and Casilin has zero rows in the current 199-product
Orderchamp import list.

**Read on what happened:** Casilin's storefront was connected on
Orderchamp and did a one-time product import on 2026-08-03 (correctly
seeding the barcode/SKU/variant data the audit measured), then the
Orderchamp connection itself was drafted/paused very shortly after (by
Orderchamp's own automation, not a person) and has stayed unpublished since
-- matching your note that Casilin is currently in vendor negotiation
outside Orderchamp. The 32 live products are a frozen snapshot from that
one import, manually published by a person days later, with no ongoing
sync since. This is the "regular timestamps then it stops" pattern for
automation, exactly as you described distinguishing signal.

**Other sources checked and ruled out:**
- Installed-apps list (Settings > Apps, "Installed apps" tab) has 11 apps:
  Shopify Knowledge Base, Theme Kit Access, Translate & Adapt, Klarna
  On-Site Messaging, Merchant Feed Sync (this repo, `read_products` only
  per its own README), **Orderchamp**, mdc-blog-pipeline,
  Judge.me, Trustpilot, Klaviyo, and claude-code-content-admin. None of
  these besides Orderchamp plausibly write vendor/barcode/variant catalog
  data -- the rest are theme tooling, translations, payments messaging, a
  read-only feed reader, content/SEO, reviews, or marketing.
- This repo (searched all of `250118_MaisonDeCocon`, excluding archived/
  transfer folders): "Casilin" appears only in `build_feed.py` code
  comments (documenting a known data quirk -- some Casilin products lack a
  Color option) and in generated feed CSVs/reports -- **no script, GitHub
  Action, or config anywhere touches Casilin specifically**. The Cerdá
  pipeline (`cerda-sync/`) has zero Casilin references.

**Conclusion, stated plainly per your ask:** the source of truth for
Casilin's *existing* 32 products is a one-time Orderchamp import from
2026-08-03 that has not received any automated update since. There is no
second, currently-active integration for Casilin. Any future data (GTIN/
weight/dimension backfills) will need either (a) the Orderchamp connection
being republished/reconnected once the vendor negotiation resolves, or (b)
manual sourcing, same as Coco & Cici -- **not written or assumed here,**
per your instruction not to guess at a future sourcing arrangement.

## 3. Boomba/MoST dimension scrape

**No Shopify writes made in this section -- proposal only, awaiting review.**

### Method: don't scrape all 113, exploit the pattern first

Rather than opening ~113 individual Orderchamp listing pages, I tested
whether package dimensions/weight are determined by **product type**
rather than color/size, since Orderchamp already showed (in the prior
session's sampling) that one listing publishes ONE spec block regardless
of its own internal size sub-variants. Sampled 7 Boomba listings across 4
product types x 2 colors each (plus a 1-person/2-person Bedding Set pair):
every same-type pair matched exactly, regardless of color. That
collapses Boomba's 55-product scrape into 4 reference lookups. MoST did
**not** collapse as cleanly -- see below.

### Boomba Bamboo -- fully resolved, all 55 products, high confidence

| Product type | Length | Width | Height | Weight | Verified via (2 colors + bedding-set size check) |
|---|---|---|---|---|---|
| Fitted Sheets (incl. "for Mattress Topper" and "Split-Topper" subtypes) | 40cm | 30cm | 6cm | 800g | Coffee Brown vs Soft Taupe (regular); Coco White (mattress-topper subtype) -- all 3 identical |
| Duvet Covers | 40cm | 30cm | 13cm | 2,200g | Coco White vs Deep Moss -- identical |
| Pillowcases (Set of 2) | 30cm | 20cm | 5cm | 400g | Coco White vs Deep Moss -- identical |
| Bedding Sets (1-person AND 2-person, all colors) | 40cm | 30cm | 40cm | *(not published by Orderchamp -- still a genuine gap)* | Sage Green 1-person vs Deep Moss 2-person -- identical carton dims despite different content count |

This resolves **all 53 Boomba products from the prior report's JSON list +
the 2 additional Bedding Sets** (`white-bamboo-bedding-set-2-person...` and
`light-green-bamboo-bedding-set-1-person...`) = **55/55 Boomba products**.
Proposed writes: `maison_seo.height/width/length` metafield per variant
(same value across every variant under a product, since Orderchamp doesn't
differentiate by sub-size) for all 55 products; `inventoryItem.measurement.weight`
native field for the 37 non-Bedding-Set products (Fitted Sheets/Duvet
Covers/Pillowcases have a published weight; the 18 Bedding Sets don't --
consistent with them already being flagged `custom_product=true` for GTIN
in Part 1, i.e. this SKU family has multiple source-data gaps, not just
GTIN). Not pushed -- awaiting your review of the table above before I
generate the actual mutation list (867 variant-level writes is a lot to
run blind; confirm the methodology first).

### MoST Blankets -- partially resolved, methodology proven but not scraped to completion

Live GraphQL re-check: **60/60 active MoST products confirmed
`maison_seo.height = null`**, matching the audit. Structure is much less
uniform than Boomba's, so the "sample a few, apply to all" shortcut only
partly works:

- **Throws (~45 of the 60 products, single-variant, size baked into the
  Shopify title, e.g. "140x200")**: DO have a clean Length/Width/Weight
  spec block per listing (no Height -- flat items), but **weight varies by
  weave family, not just size** -- confirmed two different 140x200 Throws:
  plain Wool weave (Venezia Grey) = 800g, Bouclé weave (Paris Dark Red) =
  1,050g. So Throws need one reference scrape per (weave-family x size)
  combination, not one per size. Not fully enumerated -- only these 2 of
  an estimated 6-8 weave families sampled.
- **Bedspreads (~11 products) and Bed Runners (~3 products)**: **do NOT
  have a structured spec block at all.** Checked "Merino wool bed blanket
  SUN DUST" (Bedspread) -- Orderchamp shows no Length/Width/Height/Weight
  fields in Product Specifications; weight only appears as unstructured
  prose inside the description ("Weight: 600 gr/m2", i.e. fabric density,
  not a per-item gram figure) -- the same unstructured-text problem
  already identified as the root cause of the whole dimensions gap in the
  prior investigation. A Bed Runner sample attempt hit a product that
  wasn't actually on the import list (mismatched match, not representative
  -- discarded rather than reported as data).

**What this means for the diff:** Boomba's table above is ready to act on.
MoST needs one more pass to (a) sample the remaining ~4-6 weave families
for Throws (a further ~8-12 listing visits, same fast method) and (b) for
Bedspreads/Bed Runners, compute weight from the description's g/m² figure
x the known area (L x W already in the Shopify variant size, same
scale-by-area technique already validated for Coco & Cici in the prior
report) rather than expecting Orderchamp to publish a clean number --
Orderchamp genuinely doesn't have one for these two categories. Not done
here to avoid guessing at a diff without the remaining weave-family data
points; flagging as the next concrete step rather than padding this report
with an incomplete table.

### Coverage against the audit's 182-product dimension gap

- Boomba: 55/55 closed (proposal ready for review)
- MoST: 0/60 closed yet (root cause and method proven, ~45 Throws need
  4-6 more reference scrapes, ~14 Bedspreads/Bed Runners need the
  g/m²-x-area computation approach applied per product)
- Coco & Cici (39) and Casilin (32, wrong pipeline entirely -- see Part 2):
  out of scope for this Orderchamp-side fix, per the prior report.
- **55 of 182 (30%) has a reviewable proposal today; the remaining 127
  needs either more scrape time (MoST) or manual sourcing (Coco & Cici) or
  is blocked on a vendor-relationship decision (Casilin).**

**No Shopify writes were made in this entire section.** Everything above
is a proposal for human review.
