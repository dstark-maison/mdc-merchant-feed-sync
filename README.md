# mdc-merchant-feed-sync

Builds a Google Merchant Center product feed from Maison de Cocon's Shopify
catalog: SKU, title, price, availability, image (plus up to 10
`additional_image_link` photos per product, comma-joined per Google's
text-feed format), GTIN/MPN, and `description` mapped from `body_html` (HTML
stripped). Every row goes through a validation
gate (missing required fields are excluded and logged, never silently
published) and a hard reject for known Google sample/placeholder values --
the root cause the account was previously flagged for.

**Status as of 2026-08-21: live.** The "Merchant Feed Sync" Shopify app
(`read_products` only) is created and installed, the pipeline has run
against the full live catalog (867 offers / 224 products, 0 exclusions, 0
sample-data rejects), this repo is public and serves the feed at
`https://raw.githubusercontent.com/dstark-maison/mdc-merchant-feed-sync/master/data/google_merchant_feed.csv`,
both GitHub Actions schedules are enabled (daily build 06:00 UTC, weekly
digest Mondays 07:00 UTC), and `RESEND_API_KEY` is configured. Remaining
steps are all on the Merchant Center side -- see the go-live checklist below.

## What it does

1. **Pulls product data** two ways, sharing one transform/validate/write
   pipeline so neither path can silently diverge in behavior:
   - `--source csv <path>`: reads a Shopify "Export products" CSV (Products →
     Export in Shopify Admin). Used for offline testing and for a manual
     first upload if you ever need one.
   - `--source shopify-api`: reads live product data from the Shopify Admin
     GraphQL API using OAuth client-credentials -- same auth pattern
     `cerda-sync/sync_angel_cerda.py` uses, because Dev Dashboard apps
     (created since Jan 2026) don't expose a static Admin API token.
2. **Maps `body_html` → `description`** (HTML stripped) for every offer/variant row.
3. **Validation gate**: a row missing any required field (id, title,
   description, link, image_link, price, availability, brand, or a
   checksum-valid gtin/mpn) is excluded from the feed and logged with a
   specific reason in `data/*_exclusions.csv` -- never silently published
   with a blank value.
4. **Sample-data hard check**: rejects any row matching known values from
   Google's own Merchant Center / Content API documentation sample feeds
   (id `1111111111`, title "Mens Pique Polo Shirt", brand "Google",
   `example.com` links, all-zero GTINs, etc.) -- logged separately from
   ordinary validation failures, since this is the specific failure mode
   that caused the original account suspension.
5. **Shipping via `shipping_label`, NOT per-row shipping or return policy.**
   Each row carries a `shipping_label` from `shipping_label_for()` in
   `build_feed.py` (the only place vendors are listed): std_9 Boomba Bamboo,
   std_990 MoST Blankets, std_10 Coco & Cici, std_15 VIVARAISE, `sf_bulky` for
   every SalesFever product (a `sf_small` split for the 6 Bed Benches was
   tried and reverted 2026-09-26 -- pending rate confirmation, see
   `SALESFEVER_SMALL_TYPES` in `build_feed.py`), and `std_default` for any
   unmapped vendor. There is NO per-row
   `shipping` cell: shipping cost and delivery time come only from the
   account-level Merchant Center shipping services, each filtered to one label.
   To onboard a vendor, add its label here AND create its GMC service.
   Return policy is likewise account-level only (Verified "Standard for
   Germany" policy); expanding return coverage to AT/FR/BE/LU is a manual
   Merchant Center follow-up.

## Feed hosting

Repo is **public** and the generated feed is served straight from
`raw.githubusercontent.com` -- no separate hosting infra, no auth. This was
the simplest of the three options considered (vs. a second private-repo+Pages
setup, vs. Basic Auth on the URL) and meets Google's scheduled-fetch
requirements (HTTPS URL, no Googlebot/AdsBot-Google blocking, well under the
4GB size cap). The only tradeoff is that this repo's *code* (not the feed
data, which already mirrors the public storefront) is now publicly visible --
no secrets are ever committed here, only referenced by name, so this is safe.

Feed URL (matches the existing manual upload's CSV format, so Merchant
Center's file-format setting doesn't need to change):
```
https://raw.githubusercontent.com/dstark-maison/mdc-merchant-feed-sync/master/data/google_merchant_feed.csv
```

## One-time setup (done 2026-08-21)

1. ~~Create a new Shopify custom app~~ Done: "Merchant Feed Sync" app created
   in the Dev Dashboard, `read_products` only, installed on the store.
2. ~~Add four repository secrets~~ Done: `SHOPIFY_STORE_DOMAIN`,
   `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `RESEND_API_KEY` all set.
3. ~~Decide feed hosting~~ Done: Option A (public repo, raw URL) -- see above.
4. ~~Uncomment the `schedule:` blocks~~ Done in both workflows.
5. **Configure the Merchant Center scheduled fetch** -- the one remaining
   manual step, UI-only (confirmed by walking the actual UI on 2026-08-21;
   it does **not** match the generic "edit an existing source's schedule"
   flow some Google help docs describe -- this account's data-sources UI has
   no method-switch option on an existing source, only re-upload/SFTP or
   delete):
   1. Merchant Center → Settings → Data sources → **Add product source**
   2. Choose **"Add products from a file"** (not "Connect to Shopify" --
      see note below on why)
   3. Leave **"Enter a link to your file"** selected → paste:
      `https://raw.githubusercontent.com/dstark-maison/mdc-merchant-feed-sync/master/data/google_merchant_feed.csv`
   4. **Edit schedule** → Daily, any time after 06:05 UTC (the wizard's
      schedule editor uses your account's local timezone, not UTC --
      convert accordingly)
   5. **Add authentication information** → leave as "No username and
      password provided" (the URL is public, no auth needed)
   6. Continue through the rest of the wizard: Countries **Germany**,
      Language **German** (not English -- the old manual source had this
      wrong; the feed's `link` is the storefront's bare URL, which renders
      German by default, so title/description must be German to match --
      fixed in the 2026-08-21 language-fix commit), Feed label **DE**
   7. Finish creating the source, then let it run its first fetch
   8. **Once the new source shows ~224 products** (confirms it successfully
      pulled and took over), delete the old `google_merchant_feed.csv`
      (File, manual) source: Data sources → its row's **⋮ Actions** menu →
      **Delete source**. See "Retiring the manual CSV upload" below for why
      deleting only after confirming the new source works avoids any gap.

   **Note on "Connect to Shopify":** the wizard's first, pre-selected option
   is Google's own native Shopify connector (via the already-installed
   "Google & YouTube" Shopify sales channel app) -- it would auto-sync
   products with zero custom pipeline. This repo exists specifically because
   the native connector doesn't give you the validation gate / sample-data
   reject / description-mapping control this pipeline enforces (the root
   cause of the original Misrepresentation suspension). Worth knowing this
   option exists, but switching to it would undo the safeguards this repo
   was built for -- not recommended without a deliberate decision to do so.
6. **Review the "genuinely empty body_html" list** that will appear in the
   first live report (`reports/YYYY-MM-DD.md`) -- these products need
   hand-written descriptions; the pipeline deliberately does not
   auto-generate them. (First live run: 0 such products.)

## Local usage

```bash
pip install -r requirements.txt
pytest tests/ -v                                   # run the test suite

# Offline / CSV mode (no credentials needed)
python build_feed.py --source csv --csv-path export.csv --out google_merchant_feed

# Live mode (needs SHOPIFY_STORE_DOMAIN / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET env vars)
python build_feed.py --source shopify-api --out google_merchant_feed

# Weekly digest (dry-run by default -- prints instead of sending)
python send_weekly_report.py
python send_weekly_report.py --send                # actually calls Resend (needs RESEND_API_KEY)
```

## Retiring the manual CSV upload

The feed currently live in Merchant Center (22 products) was populated by a
one-time manual CSV upload (`google_merchant_feed.csv`, File/manual, last
updated Aug 13 2026 -- the Layer 1 stopgap used to unblock the
Misrepresentation review). This session confirmed directly in the Merchant
Center UI (2026-08-21) that an existing manual-upload source **cannot** be
switched in place to scheduled fetch -- its only actions are re-upload,
update via SFTP, or delete. So the plan is add-new-then-delete-old, not
edit-in-place:

1. Add the new scheduled-fetch source (checklist step 5 above) as a
   *second*, separate data source.
2. Per Google's documentation on data sources, when the same product ID
   exists in more than one of your sources, it's considered to belong to
   whichever source was **most recently updated** -- no duplicates are
   created. Since the new source updates daily and the old manual one is
   now frozen, ownership of the overlapping ~22 products transfers to the
   new source on its very first successful fetch.
3. Once the new source's product count reaches ~224 (confirms it's fully
   live and has taken over), **delete the old manual `google_merchant_feed.csv`
   source** (Data sources → its row's ⋮ menu → Delete source). Deleting it
   only after confirming the new source works avoids any gap in coverage,
   and is safe at that point -- the old source no longer owns any live
   products by then.

After the first scheduled fetch succeeds, verify in Merchant Center: product
count matches the live catalog (224 products / 867 offers as of 2026-08-21),
no stale items from the old manual upload remain, and the "Improve item
appearance" issues (missing description, etc.) are trending down as the new
feed's real descriptions get processed.

## idealo feed

`idealo_feed.py` is a **standalone** sibling pipeline for the idealo
Business Center CSV import -- it imports and reuses `build_feed`'s loaders
and validators (`load_products_from_csv`, `load_products_from_shopify_api`,
`validate_row`, `is_known_sample_value`, `gtin_checksum_valid`, `ProductRow`,
`MARKETS`) so idealo and Google Merchant Center can never silently diverge
on which offers are eligible, but it never modifies `build_feed.py` and
never shares an output file, exclusions log, or report with it -- zero
regression risk to the GMC pipeline that previously resolved a
Misrepresentation suspension.

Hosted feed URL:
```
https://raw.githubusercontent.com/dstark-maison/mdc-merchant-feed-sync/master/data/idealo_feed.csv
```

**Field mapping** (idealo CSV Feed Import spec -- comma-delimited, UTF-8,
`;` for in-column lists):

| idealo column | Source |
|---|---|
| `sku` | `id` |
| `brand` | `brand` |
| `title` | `title` |
| `url` | `link` |
| `eans` | `gtin` |
| `description` | `description` |
| `price` | `price_amount` (raw decimal, no "EUR" suffix) |
| `categoryPath` | Shopify's product "Type" field, looked up by handle via idealo's own query/CSV column read -- left blank rather than guessed if the product has no Type set |
| `size` | `size` |
| `colour` | `color` |
| `imageUrls` | `image_link` + `additional_image_link`, semicolon-joined (GMC's own column stays comma-joined) |
| `delivery` | constant `"4-7 working days"` (matches this shop's GMC shipping policy: 1-2 day handling + 3-5 day transit) |
| `paymentCosts_paypal`, `paymentCosts_credit_card` | constant `"0.00"` |

**Shipping-tier logic** (`deliveryCosts_dpd`, matching the GMC/Business
Center shipping policy -- never left blank, an unparseable price falls back
to the top tier):

| Price range | Cost |
|---|---|
| €0.01 – €700.00 | €10.00 |
| €700.01 – €1,500.00 | €120.00 |
| €1,500.01+ | €300.00 |

Run locally the same way as `build_feed.py`:
```bash
python idealo_feed.py --source csv --csv-path export.csv          # offline
python idealo_feed.py --source shopify-api --market de             # live
```

Output: `data/idealo_feed.csv`, `data/idealo_feed_exclusions.csv`, and
`reports/YYYY-MM-DD_idealo.md`. Scheduled by its own workflow,
`.github/workflows/build-idealo-feed.yml`, at 06:30 UTC daily (after
`build-feed.yml`'s 06:00 UTC GMC build) -- a separate job so a failure in
one pipeline can never block or break the other.

## Go-live checklist

- [x] Create the "Merchant Feed Sync" Shopify custom app (`read_products` only)
- [x] Add the 4 GitHub repo secrets
- [x] Decide feed hosting (public repo + raw URL) and set it up
- [x] Uncomment `schedule:` in `build-feed.yml`
- [x] Uncomment `schedule:` in `weekly-report.yml`
- [ ] Configure Merchant Center's scheduled fetch pointing at the hosted feed URL (manual UI step -- see "One-time setup" step 5 above)
- [ ] Delete the old manual-upload source once the new one shows ~224 products (see "Retiring the manual CSV upload" above)
- [ ] After the first scheduled fetch, verify product count and reconciliation
- [ ] Review and write copy for any products flagged with empty `body_html` (0 as of the first live run)
- [ ] Separately (not this pipeline): expand return/shipping account coverage to AT/FR/BE/LU in Merchant Center if selling there
