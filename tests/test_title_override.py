import build_feed
import title_override as to


def t(raw, vendor, brand, size="", cur=None, ptype=""):
    return to.build_title(raw, cur if cur is not None else raw, vendor, brand, size, ptype)


def test_size_lookup_matches_german_option_names():
    opts = {"komfort": "Weich", "größe": "40×80 cm"}  # RIBECO pillow
    assert build_feed._option_value_containing(opts, "size") == "40×80 cm"
    for name in ("grösse", "groesse", "maße", "format", "choose your size"):
        assert build_feed._option_value_containing({name: "1"}, "size") == "1"
    assert build_feed._option_value_containing({"komfort": "Weich"}, "size") == ""
    assert build_feed._option_value_containing({"farbe": "x", "colour": "Beige"}, "colour") == "Beige"


def test_ribeco_topper_size_then_brand():
    assert t("Gelschaum-Topper", "RIBECO", "RIBECO", "180×200 cm") == ("Gelschaum-Topper 180x200 – RIBECO", [])


def test_height_kept_with_cm_after_size():
    new, flags = t("Hybridschaum Wendematratze 24 cm", "RIBECO", "RIBECO", "140×200 cm")
    assert new == "Hybridschaum Wendematratze 140x200 24 cm – RIBECO" and flags == []


def test_quoted_model_name_follows_size_and_title_size_moves():
    new, _ = t("Woll-Decke „Hunter“ 140 x 200", "MoST Blankets", "MoST Blankets", "")
    assert new == "Wolldecke 140x200 „Hunter“ – MoST Blankets"


def test_variant_size_wins_and_mismatch_is_flagged():
    new, flags = t("Woll-Decke „Hunter“ 140 x 200", "MoST Blankets", "MoST Blankets", "200 x 220 cm")
    assert new == "Wolldecke 200x220 „Hunter“ – MoST Blankets" and flags == ["size-mismatch:title=140x200"]


def test_salesfever_no_suffix_and_comma_split():
    new, flags = t("Muschelbett aus Samt, Lila, 140×200", "SalesFever", "SalesFever")
    assert new == "Muschelbett aus Samt 140x200 Lila" and flags == []


def test_bed_bench_without_size_is_not_flagged():
    new, flags = t("Bettbank mit Stauraum aus Bouclé in Taupe", "SalesFever", "SalesFever", "", ptype="Bed Benches")
    assert new == "Bettbank mit Stauraum aus Bouclé in Taupe" and flags == []


def test_no_size_anywhere_is_flagged_not_invented():
    new, flags = t("Kissenbezug aus Tencel™-Satin, weiß", "Coco & Cici", "Coco & Cici")
    assert "50x60" not in new and flags == ["no-size"]


def test_vivaraise_unchanged_when_type_and_size_up_front():
    cur = "VIVARAISE Kissen aus Baumwollsamt Fara Kaffee 30×50"
    assert t("Kissen aus Baumwollsamt Fara Kaffee 30×50", "VIVARAISE", "VIVARAISE", "30×50 cm", cur=cur) == (cur, ["unchanged-vivaraise-ok"])


def test_vivaraise_reordered_when_size_late():
    raw = "Tagesdecke aus gesteppter Baumwolle mit Wellenmuster in der Farbe Terracotta Michka 220×240"
    new, flags = t(raw, "VIVARAISE", "VIVARAISE", "220×240 cm", cur="VIVARAISE " + raw)
    assert new == "Tagesdecke aus gesteppter Baumwolle mit Wellenmuster in der Farbe Terracotta Michka 220x240 – VIVARAISE"
    assert "size-late" in flags


def test_max_150_keeps_head_size_brand():
    raw = "Decke " + "Wort " * 40 + "Ende"
    new, flags = t(raw, "MoST Blankets", "MoST Blankets", "140×200")
    assert len(new) <= 150 and new.endswith(" – MoST Blankets") and "truncated" in flags


def test_brand_prefix_not_duplicated():
    new, _ = t("MoST Blankets Wolldecke", "MoST Blankets", "MoST Blankets", "140×200 cm")
    assert new == "Wolldecke 140x200 – MoST Blankets"


def test_woll_compounds_normalised():
    for raw, want in (("Woll-Überwurfdecke „Savana“ 200x220", "Wollüberwurfdecke 200x220 „Savana“"),
                      ("Woll-Kuscheldecke Alba 140x200", "Wollkuscheldecke Alba 140x200"),
                      ("Woll-Bouclé-Decke „Paris“ 140x200", "Wollbouclé-Decke 140x200 „Paris“")):
        assert t(raw, "MoST Blankets", "MoST Blankets")[0] == want + " – MoST Blankets"


def test_boomba_is_held_and_staged_apply():
    cur = "Boomba Bamboo Bettbezug aus Bambus, Soft Taupe, 400TC"
    assert t("Bettbezug aus Bambus, Soft Taupe, 400TC", "Boomba Bamboo", "Boomba Bamboo", "140×200", cur=cur) == (cur, ["held-boomba"])
    assert to.applies_to("RIBECO") and to.applies_to("MoST Blankets") and to.applies_to("SalesFever")
    assert not any(to.applies_to(v) for v in ("Boomba Bamboo", "VIVARAISE", "Coco & Cici", "Maison de Cocon"))
