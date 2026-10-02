from normalize import is_po_box, name_similarity, norm_name, norm_phone, norm_street, norm_zip


def test_street_long_and_short_forms_are_equal():
    assert norm_street("4850 Northwest Sylvania Avenue") == norm_street("4850 NW Sylvania Ave")
    assert norm_street("199 Barks Road West") == norm_street("199 Barks Rd W")
    assert norm_street("3313 Wilmington Pk") == norm_street("3313 Wilmington Pike")


def test_different_streets_stay_different():
    assert norm_street("118 Union Square Dr") != norm_street("240 Market St")


def test_po_box():
    assert is_po_box("PO Box 517") and is_po_box("P.O. Box 9")
    assert not is_po_box("3156 W Prospect Rd")


def test_name_spelling_variants_are_equal():
    assert norm_name("Bellhaven Rehab and Nursing of Grove City") == norm_name("Bellhaven Rehabilitation & Nursing of Grove City")
    assert norm_name("Arbors at Bellhaven Dayton") == norm_name("The Arbors at Bellhaven - Dayton")
    assert norm_name("Bellhaven Health Care Center of Ashland") == norm_name("Bellhaven Healthcare Centre of Ashland")


def test_a_rebrand_is_not_a_similar_name():
    assert name_similarity("Riverbend Manor Care Center", "Bellhaven of Chagrin Falls") < 0.5


def test_phone_and_zip():
    assert norm_phone("(614) 250-9447") == "6142509447"
    assert norm_zip("45662-1234") == "45662"
