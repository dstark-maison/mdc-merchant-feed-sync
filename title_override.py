"""Google feed title override -- DE market only (serves DE + AT).

Target shape:  {Produkttyp} {Material} {Größe} {Farbe/Modellname} – {Marke}

Feed-only: Shopify product titles, descriptions and the idealo feed are never touched. Everything is derived from
facts already in the data -- no mapping tables, no English fabric text, nothing invented:

* Produkttyp / Material / Farbe / Modellname: only the words of the product's German title, in their authored order.
  The head of the title (up to the first comma, " - " / " – " or opening quote) stays in front; the size is inserted
  right behind it; the rest (colour / model name) follows. A title with no such boundary gets the size at its end.
* Größe: the variant's size option (German or English option name), normalised to "180x200" (no "cm"); a height such
  as "24 cm" is kept with its "cm" and follows the size. No size option -> the size already in the German title.
  Neither -> the title keeps no size and the row is flagged (bed benches are exempt: they have no size by design).
* Marke: " – {brand}" at the end for consumer brands and RIBECO. Never for SalesFever (GTIN owner only, not a
  storefront brand, see brands.yaml).
* VIVARAISE: titles are only reordered when type/size are not already within the first 70 characters; otherwise they
  are returned unchanged.
* Max 150 characters: only the tail is shortened, never the head, size or brand.
"""
import re

# Staged rollout: only these vendors' feed titles are replaced; every other vendor is computed + reported only.
# Stage 1 (2026-11-25): RIBECO, MoST Blankets, SalesFever. Stage 2: "Coco & Cici" (after 24 h without new disapprovals).
# VIVARAISE is unchanged by rule 7. Boomba Bamboo is HELD (bamboo->viscose relabel, EU 1007/2011): never overridden.
APPLY_VENDORS = {"RIBECO", "MoST Blankets", "SalesFever"}
HELD_VENDORS = {"Boomba Bamboo"}
TITLE_OVERRIDE_APPLY = bool(APPLY_VENDORS)  # kept for callers/tests: False would mean pure dry run


def applies_to(vendor):
    return (vendor or "").strip() in APPLY_VENDORS and (vendor or "").strip() not in HELD_VENDORS

# "Woll-Decke" -> "Wolldecke" etc. (feed titles only). Exactly these compounds, nothing broader.
WOLL_COMPOUNDS = (("Woll-Bouclé-Decke", "Wollbouclé-Decke"), ("Woll-Überwurfdecke", "Wollüberwurfdecke"),
                  ("Woll-Kuscheldecke", "Wollkuscheldecke"), ("Woll-Decke", "Wolldecke"),
                  ("Woll-Plaid", "Wollplaid"))

MAX_TITLE_LEN = 150
FRONT_WINDOW = 70
NO_SUFFIX_VENDORS = {"SalesFever"}
SIZE_OPTION_NAMES = ("size", "größe", "grösse", "groesse", "maße", "masse", "maß", "format")
SUFFIX_SEP = " – "
NO_SIZE_EXEMPT_PRODUCT_TYPES = {"Bed Benches", "Bed Bench", "Bettbänke", "Bettbank"}

_X = r"[x×X]"
DIM_RE = re.compile(rf"(?<![\d.,])(\d{{1,3}}(?:[.,]\d)?)\s*{_X}\s*(\d{{1,3}}(?:[.,]\d)?)(?:\s*{_X}\s*(\d{{1,3}}))?(?:\s*cm\b)?",
                    re.IGNORECASE)
HEIGHT_RE = re.compile(r"(?<![\d.,x×X])(\d{1,2})\s*cm\b", re.IGNORECASE)
_SEP_RE = re.compile(r"\s+[–—-]\s+|,\s*")
_QUOTES = "„“”\"«»‚‘"
_EXTRA_RE = re.compile(r"pre-?order\s*:?|\(.*?\)|\bcm\b|[+:/,\s]", re.IGNORECASE)


def normalise_dims(text):
    """First dimension in `text` -> ("180x200", residual_text) or ("", text). Decimal comma/point kept as written."""
    m = DIM_RE.search(text or "")
    if not m:
        return "", text or ""
    dims = "x".join(g for g in m.groups() if g)
    return dims, (text[:m.start()] + " " + text[m.end():]).strip()


def _clean(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s*,\s*,", ",", text)
    return text.strip(" ,-–—|/")


def _split_head_tail(core):
    """(head, tail): head ends before the first quote / comma / dash separator; no boundary -> (core, "")."""
    cut = None
    for m in re.finditer(rf"\s[{re.escape(_QUOTES)}]", core):
        cut = m.start()
        break
    sep = _SEP_RE.search(core)
    if sep and (cut is None or sep.start() < cut):
        return core[:sep.start()].strip(), core[sep.end():].strip()
    if cut is not None:
        return core[:cut].strip(), core[cut:].strip()
    return core.strip(), ""


def _strip_brand(title, brand):
    key = re.sub(r"[\W_]+", "", brand.casefold())
    words = title.split()
    for k in range(len(words), 0, -1):
        if re.sub(r"[\W_]+", "", " ".join(words[:k]).casefold()) == key:
            return " ".join(words[k:])
    return title


def build_title(raw_title, current_title, vendor, brand, variant_size, product_type=""):
    """-> (new_title, flags list). `raw_title` is the German Shopify title, `current_title` the title the feed carries
    today (brand-prefixed for consumer brands), `variant_size` the variant's size option value ("" if none)."""
    flags = []
    raw_title = (raw_title or "").strip()
    if not raw_title:
        return current_title, ["no-title"]

    if vendor in HELD_VENDORS:
        return current_title, ["held-boomba"]
    core = _strip_brand(raw_title, brand) if brand else raw_title
    for old, new in WOLL_COMPOUNDS:
        core = core.replace(old, new)
    title_size, core = normalise_dims(core)
    height = ""
    hm = HEIGHT_RE.search(core)
    if hm:
        height = f"{hm.group(1)} cm"
        core = core[:hm.start()] + " " + core[hm.end():]
    core = _clean(core)

    opt_size, opt_rest = normalise_dims(variant_size)
    if variant_size and not opt_size:
        flags.append(f"size-not-dimension:{variant_size.strip()}")
    elif opt_size and _EXTRA_RE.sub("", opt_rest):
        flags.append(f"size-option-extra:{variant_size.strip()}")
    size = opt_size or title_size
    if opt_size and title_size and opt_size.replace(",", ".") != title_size.replace(",", "."):
        # Owner ruling 2026-11-25: do NOT trust the variant size here; the title's size stays as-is until the
        # product/variant data is sorted out (read-only investigation pending). The row is only reordered.
        flags.append(f"size-mismatch:variant={opt_size}")
        size = title_size
    if not size and (product_type or "") not in NO_SIZE_EXEMPT_PRODUCT_TYPES and not height:
        flags.append("no-size")

    if vendor == "VIVARAISE":
        # Rule 7: leave the title alone when type and size are already up front.
        m = DIM_RE.search(current_title or "")
        if m and m.end() <= FRONT_WINDOW:
            return current_title, flags + ["unchanged-vivaraise-ok"]

    head, tail = _split_head_tail(core)
    size_part = " ".join(p for p in (size, height) if p)
    body = " ".join(p for p in (head, size_part, tail) if p)
    suffix = "" if (vendor in NO_SUFFIX_VENDORS or not brand) else f"{SUFFIX_SEP}{brand}"
    title = body + suffix
    if len(title) > MAX_TITLE_LEN:
        flags.append("truncated")
        room = MAX_TITLE_LEN - len(suffix)
        front = " ".join(p for p in (head, size_part) if p)
        cut = body[:room + 1]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut[:room]
        if len(cut) < len(front):
            cut = front[:room]
        title = cut.rstrip(" ,-–|/+&") + suffix
    m = DIM_RE.search(title)
    if size and (not m or m.end() > FRONT_WINDOW):
        flags.append("size-late")
    return title, flags


def write_report(records, path):
    """Audit CSV: item_id, brand, old_title, new_title, changed (y/n), flag. Sorted by brand, flagged rows first."""
    import csv
    from pathlib import Path
    def real_flag(r):  # informational flags are not problems
        return [f for f in r["flag"].split(";") if f and f not in ("unchanged-vivaraise-ok", "held-boomba")]
    rows = sorted(records, key=lambda r: (r["brand"].casefold(), 0 if real_flag(r) else 1, r["old_title"], r["item_id"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "brand", "old_title", "new_title", "changed", "applied", "flag"])
        w.writeheader()
        w.writerows(rows)
