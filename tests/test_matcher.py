"""One test per situation the matcher has to classify."""
from fake_crm import OPERATOR, account, location, parents
from matcher import build_plan


def plan_for(locations, accounts, contacts=()):
    return build_plan(locations, parents() + accounts, contacts, today="2026-10-02")


def only(plan):
    assert len(plan["proposals"]) == 1, [p.title for p in plan["proposals"]]
    return plan["proposals"][0]


def test_confident_match_proposes_nothing():
    plan = plan_for([location("Bellhaven of Akron", "55 Ridgewood Avenue")],
                    [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave")])
    assert plan["proposals"] == []
    assert plan["matches"][0]["account_id"] == "A1"
    assert plan["matches"][0]["confidence"] == "high"


def test_outdated_name_is_fixed():
    p = only(plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                      [account("A1", "Riverbend Manor", "55 Ridgewood Ave")]))
    assert p.action == "update"
    assert p.ops[0]["fields"] == {"name": "Bellhaven of Akron"}


def test_zip_typo_is_fixed():
    p = only(plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave", zip_code="44313")],
                      [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave", zip_code="44331")]))
    assert p.ops[0]["fields"] == {"billing_zip": "44313"}


def test_po_box_matches_by_name_and_is_not_overwritten():
    plan = plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                    [account("A1", "Bellhaven of Akron", "PO Box 517")])
    assert plan["proposals"] == []
    assert plan["matches"][0]["account_id"] == "A1"


def test_wrong_parent_without_open_ar_is_reparented_directly():
    p = only(plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                      [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave", parent="CEDAR", revenue=47000, ar=0)]))
    assert p.action == "reparent"
    assert p.ops[0]["fields"] == {"parent_id": OPERATOR}


def test_open_ar_without_revenue_history_is_reparented_directly():
    p = only(plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                      [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave", parent="CEDAR", revenue=0, ar=500)]))
    assert p.action == "reparent"


def test_wrong_parent_with_revenue_and_open_ar_follows_the_chow_sop():
    p = only(plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                      [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave", parent="CEDAR", revenue=84000, ar=12400)]))
    assert p.action == "chow"
    create, link = p.ops
    assert create["op"] == "create" and create["fields"]["parent_id"] == OPERATOR
    assert link == {"op": "patch", "account_id": "A1", "fields": {"chow_current_account": "$NEW"}, "expect": link["expect"]}
    assert all("parent_id" not in op["fields"] for op in p.ops if op["op"] == "patch")  # old account never re-parented


def test_two_accounts_for_one_facility_keeps_the_operator_one():
    plan = plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                    [account("OLD", "Cedar Trail of Akron", "55 Ridgewood Avenue", parent="CEDAR"),
                     account("A1", "Bellhaven of Akron", "55 Ridgewood Ave")])
    p = only(plan)
    assert p.action == "duplicate" and p.account_id == "OLD"
    assert p.ops[0]["fields"] == {"duplicate_of_account": "A1", "status": "Inactive"}


def test_tie_between_candidates_is_flagged_low_confidence():
    plan = plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                    [account("X1", "Akron Care Centre", "55 Ridgewood Ave", parent="CEDAR"),
                     account("X2", "Akron Nursing", "55 Ridgewood Ave", parent="")])
    assert {p.confidence for p in plan["proposals"]} == {"low"}
    assert plan["matches"][0]["account_id"] == "X1"  # the one with a parent account wins the tie


def test_location_with_no_account_is_created():
    p = only(plan_for([location("Bellhaven of Batavia", "2000 Hospital Dr", city="Batavia", zip_code="45103")], []))
    assert p.action == "create" and p.confidence == "high"
    assert p.ops[0]["fields"]["parent_id"] == OPERATOR


def test_same_name_in_another_state_is_not_a_match():
    p = only(plan_for([location("Amberly Manor", "4390 Darrow Rd", city="Hudson", state="OH", zip_code="44236")],
                      [account("CO1", "Amberly Manor", "918 S Nevada Ave", city="Colorado Springs", state="CO",
                               zip_code="80903", parent="CEDAR")]))
    assert p.action == "create" and p.confidence == "medium"  # medium: a near miss was looked at


def test_similar_name_in_same_city_but_different_street_is_not_a_match():
    p = only(plan_for([location("Bellhaven at Union Square", "118 Union Square Dr", city="New Albany", zip_code="43054")],
                      [account("U1", "Union Square Senior Living", "240 Market St", city="New Albany",
                               zip_code="43054", parent="CEDAR")]))
    assert p.action == "create"


def test_operator_account_missing_from_website_is_flagged_not_moved():
    p = only(plan_for([], [account("A1", "Bellhaven of Alliance", "1785 Freshley Ave")]))
    assert p.action == "needs_review"
    assert p.ops[0]["fields"] == {"status": "Needs Review"}


def test_sold_facility_with_open_ar_is_linked_to_the_new_owner_account():
    plan = plan_for([], [account("A1", "Bellhaven of Sandusky", "2715 Columbus Ave", revenue=130000, ar=5200),
                         account("M1", "Millstone Care of Sandusky", "2715 Columbus Ave", parent="CEDAR")])
    p = only(plan)
    assert p.action == "chow_link"
    assert p.ops[0]["fields"] == {"chow_current_account": "M1"}  # nothing else on the old account changes


def test_sold_facility_without_open_ar_becomes_a_duplicate_of_the_new_owner_account():
    plan = plan_for([], [account("A1", "Bellhaven of Sandusky", "2715 Columbus Ave"),
                         account("M1", "Millstone Care of Sandusky", "2715 Columbus Ave", parent="CEDAR")])
    assert only(plan).ops[0]["fields"] == {"duplicate_of_account": "M1", "status": "Inactive"}


def test_accounts_already_resolved_are_left_alone():
    plan = plan_for([location("Bellhaven of Akron", "55 Ridgewood Ave")],
                    [account("A1", "Bellhaven of Akron", "55 Ridgewood Ave"),
                     account("OLD", "Cedar Trail of Akron", "55 Ridgewood Ave", parent="CEDAR",
                             duplicate_of_account="A1", status="Inactive")])
    assert plan["proposals"] == []
