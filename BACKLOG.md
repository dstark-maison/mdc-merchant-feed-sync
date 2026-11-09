# Backlog

## Post-campaign (after Black Week / Christmas 2026)

- **Remove the campaign rebuild crons** from `.github/workflows/build-feed.yml` (`47 */2 20-30 11 *` and
  `47 */2 1-27 12 *`) after 27 Dec 2026. They are date-bound only by month/day, not by year, so left in place they
  would fire again from 20 Nov 2027. Also delete the matching cron-job.org 2-hourly dispatch job (expires 27 Dec 22:05 UTC).
- **Translate idealo `size` / `colour` attribute values to German.** Some rows carry English values straight from
  Shopify options (e.g. size "60x70 without valance", colour "White", "Light Grey/White", "Cuddle Pink"). idealo.de shows
  them verbatim. Not done before the campaign on purpose (low risk, large row count); needs an explicit mapping table
  like `CATEGORY_PATHS_DE`, with unmapped values reported rather than guessed.
- **Missing EANs**: 54 Boomba bedding-set rows (no barcode anywhere, see `reports/gtin_flagged_boomba.csv`) and 14
  VIVARAISE/MoST rows (see `mdc-ops/exports/missing-ean-2026-11.csv`) are in the feed without an EAN until the
  suppliers deliver real GTINs and the Shopify barcode is filled.
