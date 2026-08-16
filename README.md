# mdc-merchant-feed-sync

Builds a Google Merchant Center product feed from Maison de Cocon's Shopify
catalog: SKU, title, price, availability, image, GTIN/MPN, and `description`
mapped from `body_html` (HTML stripped). Every row goes through a validation
gate (missing required fields are excluded and logged, never silently
published) and a hard reject for known Google sample/placeholder values --
the root cause the account was previously flagged for.

**Status as of 2026-08-16: built and tested, not connected to anything live.**
No Merchant Center data source has been created or pointed at this repo's
output, no GitHub Actions schedule is enabled, and no email has actually been
sent (see "Go-live checklist" below for the exact remaining steps). This was
a deliberate build constraint for this session, not an oversight -- see
"Why nothing is live yet".

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
5. **Does NOT do per-row return-policy or shipping-rate mapping.** Phase 1
   diagnosis (2026-08-16) found `hasMerchantReturnPolicy` and `shippingRate`
   are already satisfied at the Merchant Center **account** level -- a
   Verified "Standard for Germany" return policy and a Complete DE shipping
   service, both already applied to all 22 products. This pipeline
   deliberately does not duplicate that per row. It currently only covers
   Germany; **expanding return/shipping coverage to AT/FR/BE/LU in Merchant
   Center account settings is a manual follow-up for Daniel**, unrelated to
   this pipeline.

## Why nothing is live yet

This repo was built and tested against real (but non-production-credentialed)
data end-to-end: `tests/test_build_feed.py` covers the CSV path against a
fixture built from real observed catalog data, and the Shopify API path
against a mocked GraphQL response (no live Shopify credentials exist for this
pipeline yet -- see step 1 below). `send_weekly_report.py` defaults to a dry
run and only actually calls Resend if both `--send` is passed AND
`RESEND_API_KEY` is set -- neither is true right now, so it cannot send
anything by accident. Both GitHub Actions workflows only have
`workflow_dispatch` (manual) triggers committed; their `schedule:` blocks are
present in the file as comments, not active, specifically so that pushing
this repo to GitHub can't start anything running against production on its
own.

## One-time setup (do this when you say go)

1. **Create a new Shopify custom app** in the [Dev
   Dashboard](https://dev.shopify.com/dashboard) -- name it something like
   "Merchant Feed Sync" (separate from the existing "Cerda Sync" app, so this
   pipeline's access can be scoped and revoked independently). Grant it
   **`read_products` only** -- this pipeline never writes to Shopify, unlike
   cerda-sync. Note the Client ID and Client Secret from its Settings page.
2. **Add four repository secrets** (Settings → Secrets and variables → Actions):
   - `SHOPIFY_STORE_DOMAIN` -- `zpjzx0-gy.myshopify.com` (bare domain)
   - `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` -- from step 1
   - `RESEND_API_KEY` -- your Resend account's API key (same account
     cerda-sync uses; `sync@reports.maisondecocon.com` must be a verified
     sending domain, same as cerda-sync's report emails)
3. **Decide where the feed file gets hosted** so Merchant Center can fetch it
   on a schedule. The generated feed is not sensitive (it's the same data
   already visible on the public storefront), but the *code* in this repo is
   kept private like the other pipelines, so pick one:
   - **(A) Simplest:** make this repo public and let Merchant Center fetch
     `https://raw.githubusercontent.com/dstark-maison/mdc-merchant-feed-sync/main/data/google_merchant_feed.txt`
     directly. No auth needed since GitHub raw URLs are public for public repos.
   - **(B) Keep the repo private:** stand up a tiny separate public repo (or
     a public `gh-pages` branch) that the workflow pushes just the generated
     feed file to, and serve that via GitHub Pages or its raw URL instead.
   - **(C) Keep everything private + authenticated:** Merchant Center's
     scheduled fetch supports HTTP Basic Auth on the URL. Possible, but means
     a long-lived credential is embedded in a URL Google's servers poll
     periodically -- more moving parts for not much benefit given the data
     itself isn't sensitive. Not recommended unless you have a specific
     reason to prefer it.

   This wasn't picked for you -- it's a real tradeoff (simplicity vs. keeping
   the repo itself private) and worth a quick decision before go-live.

4. **Uncomment the `schedule:` block** in `.github/workflows/build-feed.yml`
   (and, once that's been running cleanly for a few days,
   `.github/workflows/weekly-report.yml` too).
5. **Configure the Merchant Center scheduled fetch**: Merchant Center →
   Settings → Data sources → Add product source → choose **Scheduled fetch**
   → enter the stable URL from step 3 → File format: the same as the
   existing manual source (`google_merchant_feed.csv`'s current config is
   **Feed label: DE, Language: English**) → Fetch frequency: Daily → Fetch
   time: any time after the workflow's daily run (06:00 UTC, i.e. after
   ~06:05 UTC). This can run alongside the existing manual `.csv` file
   source at first if you want to compare outputs before retiring the manual
   one.
6. **Review the "genuinely empty body_html" list** that will appear in the
   first live report (`reports/YYYY-MM-DD.md`) -- these products need
   hand-written descriptions; the pipeline deliberately does not
   auto-generate them.

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

## Go-live checklist

- [ ] Create the "Merchant Feed Sync" Shopify custom app (`read_products` only)
- [ ] Add the 4 GitHub repo secrets
- [ ] Decide feed hosting (A/B/C above) and set it up
- [ ] Uncomment `schedule:` in `build-feed.yml`
- [ ] Configure Merchant Center's scheduled fetch pointing at the hosted feed URL
- [ ] Let it run for a few days, review reports, then uncomment `schedule:` in `weekly-report.yml`
- [ ] Review and write copy for any products flagged with empty `body_html`
- [ ] Separately (not this pipeline): expand return/shipping account coverage to AT/FR/BE/LU in Merchant Center if selling there
