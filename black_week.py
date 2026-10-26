"""
Black Week 2026 pricing for the feeds (Google Merchant Center + idealo).

Window: 2026-11-23 00:00 to 2026-11-30 23:59, Europe/Berlin. Date-driven -- no manual switch: every build decides
from the current time.

  * Google Merchant Center feeds (all four markets): `price` stays the regular price; from now until the window
    ends, matching rows also carry `sale_price` + `sale_price_effective_date` (Google applies the sale price only
    inside the window, so the feed can -- and must -- be published BEFORE the window opens). After the window the
    two columns are empty again.
  * idealo feed: `price` is the discounted price ONLY while the build time is inside the window (idealo has no
    effective-date attribute), so the feed has to be rebuilt right after 23 Nov 00:00 and 30 Nov 23:59 Berlin time.

Same rules as the Shopify setup (collections "Black Week 10" / "Black Week 5", automatic discounts, theme snippet
custom-black-week-pct.liquid). Keep the three in sync:
  -10 %: vendor VIVARAISE, MoST Blankets, SalesFever
  -5 %:  vendor Coco & Cici, unless the product is "Ocean Blue"
Boomba Bamboo (and everything else) is never discounted.

Ocean Blue is detected on the title (EN) OR the base handle: in the translated market feeds the title is
localized (DE "Ozeanblau"), the base handle is not.

Rounding matches Shopify (verified 2026-10-26 with draftOrderCalculate on tie prices): the discount amount is
truncated to the cent, discounted = price - floor(price * pct / 100).
"""
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
WINDOW_START = datetime(2026, 11, 23, 0, 0, tzinfo=BERLIN)
WINDOW_END = datetime(2026, 11, 30, 23, 59, tzinfo=BERLIN)

PCT_BY_VENDOR = {
    "VIVARAISE": 10,
    "MoST Blankets": 10,
    "SalesFever": 10,
    "Coco & Cici": 5,
}


def _now(now):
    return now if now is not None else datetime.now(BERLIN)


def in_window(now=None):
    """True from 23 Nov 00:00 up to and including 30 Nov 23:59 Berlin time."""
    n = _now(now)
    return WINDOW_START <= n <= WINDOW_END.replace(second=59)


def window_not_over(now=None):
    """True until the window has ended (sale fields are published ahead of the window start)."""
    return _now(now) <= WINDOW_END.replace(second=59)


def discount_pct(vendor, title="", handle=""):
    """10, 5 or 0 for a product, by the Black Week rules."""
    pct = PCT_BY_VENDOR.get((vendor or "").strip(), 0)
    if pct == 5:
        if "ocean blue" in (title or "").lower() or "ocean-blue" in (handle or "").lower():
            return 0
    return pct


def discounted_amount(price_amount, pct):
    """'29.95', 10 -> '26.96' (discount truncated to the cent, like Shopify). '' for an unusable price."""
    try:
        price = Decimal(str(price_amount).strip())
    except Exception:
        return ""
    if price <= 0 or not pct:
        return ""
    discount = (price * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return str(price - discount)


def row_pct(row):
    return discount_pct(row.get("vendor") or row.get("brand"), row.get("title", ""), row.get("handle", ""))


def effective_date_range():
    """Google's ISO 8601 interval, e.g. 2026-11-23T00:00+0100/2026-11-30T23:59+0100."""
    fmt = "%Y-%m-%dT%H:%M%z"
    return f"{WINDOW_START.strftime(fmt)}/{WINDOW_END.strftime(fmt)}"


def gmc_sale_fields(row, now=None):
    """-> (sale_price, sale_price_effective_date); both '' when the row is not discounted or the window is over."""
    pct = row_pct(row)
    if not pct or not window_not_over(now):
        return "", ""
    amount = discounted_amount(row.get("price_amount", ""), pct)
    if not amount:
        return "", ""
    return f"{amount} EUR", effective_date_range()


def idealo_price(row, now=None):
    """The idealo `price`: discounted inside the window for matching rows, else the regular price_amount."""
    regular = row.get("price_amount", "")
    pct = row_pct(row)
    if pct and in_window(now):
        return discounted_amount(regular, pct) or regular
    return regular
