# Changelog

## 2026-10-06 -- brand in feed titles + `brand` attribute (GMC and idealo)

- New `brands.yaml` mapping Shopify vendors to feed brands (see README, "Brand
  mapping"). Consumer brands (Coco & Cici, Boomba Bamboo, MoST Blankets,
  VIVARAISE) get `brand` = the brand and the title `"{Brand} {title}"`; suppliers
  are white-label ("Maison de Cocon", title unchanged); unknown vendors default
  to "Maison de Cocon" and are flagged in the build report; empty vendor is
  skipped as before.
- Brand/title overlap merge: a title starting with the brand's trailing word(s)
  drops them ("Boomba Bamboo" + "Bamboo Fitted Sheet" -> "Boomba Bamboo Fitted
  Sheet"). Titles capped at 150 chars (trailing attributes shortened first).
- SalesFever is `passthrough` (held): brand and title exactly as before, until
  the GTIN/manufacturer decision.
- Same mapping applied to `idealo_feed.py` through the shared
  `build_feed.apply_brand_mapping()`. idealo shipping/delivery tables stay keyed
  on the raw Shopify vendor.
- Shopify product titles are unchanged; feed only. Item counts unchanged.
- `brand_title_dryrun.py`: before/after diff of the last built feeds.
- Adds `pyyaml` to `requirements.txt`.
