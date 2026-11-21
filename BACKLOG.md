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

## Before Christmas rollout (1 Dec 2026): read campaign discounts live instead of hardcoding

Status: PLAN ONLY, parked until after Black Week (30 Nov). No change to the live feed logic during the campaign.
Check 2026-11-21: 19 variants (every brand, 6 RIBECO sale variants, 2 Coco & Cici Ocean Blue rows) -- feed `sale_price`
== live cart price. Ocean Blue and Boomba exclusions confirmed correct by the owner.

1. Read active + scheduled automatic discounts at build time (`discountNodes`): percentage, target collections,
   `startsAt` / `endsAt`. Needs the `read_discounts` scope on the Merchant Feed Sync app (check first; adding it is a
   Shopify app change).
2. Take membership from each target collection's products (`collection.products`) instead of the vendor + Ocean Blue
   rules in `black_week.py` (and the theme snippet `custom-black-week-pct.liquid`).
3. Keep the current hardcoded rules as a fallback. If the discount query fails, use them and flag it in the build
   report. Never emit a sale price from a half-failed query.
4. Cross-check the live read against the hardcoded rules on every build; differences go in the build report and fail
   a dry run.
5. Unchanged: Shopify rounding (discount truncated to the cent), RIBECO table path, idealo price flip, automatic drop
   after the window ends, `+0100` effective-date format.
6. Christmas: "Weihnachten -5%" is deactivated; the live read stays silent until it is reactivated, replacing the
   manual `CHRISTMAS_ENABLED` switch.
7. Tests: percentage rounding, multi-collection discount, expired, scheduled, failed query. Run a dry run and diff
   against the current feed; the diff must be empty for the Black Week rows before going live.
8. Merge to master only with owner approval (it goes live on the next build).
