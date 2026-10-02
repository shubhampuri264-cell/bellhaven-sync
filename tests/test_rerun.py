"""The safety promises: re-runs do not re-propose, nothing writes without approval,
stale proposals do not write, and the SOP cannot be bypassed."""
import pytest

import pipeline
import store
from apply import SopViolation, StaleProposal, apply_proposal
from fake_crm import OPERATOR, FakeCRM, account, location, parents


def world():
    """A small CRM with one of each problem, and the website that goes with it."""
    crm = FakeCRM(parents() + [
        account("OK", "Bellhaven of Akron", "55 Ridgewood Ave"),
        account("RENAME", "Riverbend Manor", "150 River St"),
        account("MOVE", "Bellhaven of Lima", "3115 N Cole St", parent="CEDAR", revenue=47000, ar=0),
        account("LOCKED", "Bellhaven of Tiffin", "45 St Lawrence Dr", parent="CEDAR", revenue=84000, ar=12400),
        account("DUP", "Cedar Trail of Akron", "55 Ridgewood Avenue", parent="CEDAR"),
        account("GONE", "Bellhaven of Alliance", "1785 Freshley Ave"),
    ])
    locations = [
        location("Bellhaven of Akron", "55 Ridgewood Ave"),
        location("Bellhaven of Chagrin Falls", "150 River St"),
        location("Bellhaven of Lima", "3115 N Cole St"),
        location("Bellhaven of Tiffin", "45 St Lawrence Dr"),
        location("Bellhaven of Batavia", "2000 Hospital Dr"),
    ]
    return crm, locations, store.connect(":memory:")


def run(crm, locations, conn):
    return pipeline.run(crm=crm, conn=conn, locations=locations)[1]


def approve_all(crm, conn):
    for proposal in store.list_proposals(conn, "pending"):
        store.set_status(conn, proposal["key"], "approved", "", apply_proposal(crm, proposal))


def test_pipeline_never_writes_to_the_crm():
    crm, locations, conn = world()
    stats = run(crm, locations, conn)
    assert stats["new"] == 6
    assert crm.writes == []


def test_second_run_does_not_duplicate_pending_proposals():
    crm, locations, conn = world()
    run(crm, locations, conn)
    stats = run(crm, locations, conn)
    assert stats["new"] == 0 and stats["refreshed"] == 6
    assert len(store.list_proposals(conn, "pending")) == 6


def test_after_approving_everything_the_next_run_proposes_nothing():
    crm, locations, conn = world()
    run(crm, locations, conn)
    approve_all(crm, conn)
    stats = run(crm, locations, conn)
    assert stats["generated"] == 0
    assert store.list_proposals(conn, "pending") == []


def test_rejected_proposal_is_never_proposed_again():
    crm, locations, conn = world()
    run(crm, locations, conn)
    rename = next(p for p in store.list_proposals(conn, "pending") if p["account_id"] == "RENAME")
    store.set_status(conn, rename["key"], "rejected", "name is right")
    stats = run(crm, locations, conn)
    assert stats["skipped_decided"] == 1
    assert store.get_proposal(conn, rename["key"])["status"] == "rejected"
    assert all(p["account_id"] != "RENAME" for p in store.list_proposals(conn, "pending"))


def test_chow_leaves_the_old_account_untouched_except_for_the_link():
    crm, locations, conn = world()
    before = crm.get_account("LOCKED")
    run(crm, locations, conn)
    approve_all(crm, conn)
    after = crm.get_account("LOCKED")
    new_id = after["chow_current_account"]
    assert crm.get_account(new_id)["parent_id"] == OPERATOR
    assert {k: v for k, v in after.items() if k != "chow_current_account"} == \
           {k: v for k, v in before.items() if k != "chow_current_account"}


def test_chow_retry_does_not_create_a_second_account():
    crm, locations, conn = world()
    run(crm, locations, conn)
    chow = next(p for p in store.list_proposals(conn, "pending") if p["action"] == "chow")
    crm.create_account(chow["ops"][0]["fields"])  # as if an earlier attempt created it, then crashed
    apply_proposal(crm, chow)
    tiffins = [a for a in crm.list_accounts(parent_id=OPERATOR) if a["name"] == "Bellhaven of Tiffin"]
    assert len(tiffins) == 1


def test_stale_proposal_is_refused():
    crm, locations, conn = world()
    run(crm, locations, conn)
    rename = next(p for p in store.list_proposals(conn, "pending") if p["account_id"] == "RENAME")
    crm.accounts["RENAME"]["name"] = "Someone Else Renamed It"  # the CRM changed after we proposed
    writes_before = len(crm.writes)
    with pytest.raises(StaleProposal):
        apply_proposal(crm, rename)
    assert len(crm.writes) == writes_before


def test_sop_guard_blocks_reparenting_a_billing_locked_account():
    crm, _, _ = world()
    live = crm.get_account("LOCKED")
    bad = {"ops": [{"op": "patch", "account_id": "LOCKED", "fields": {"parent_id": OPERATOR},
                    "expect": {"parent_id": live["parent_id"]}}]}
    with pytest.raises(SopViolation):
        apply_proposal(crm, bad)
    assert crm.writes == []


def test_broken_scrape_stops_the_run():
    crm, locations, conn = world()
    run(crm, locations, conn)
    with pytest.raises(pipeline.PipelineError):
        run(crm, locations[:2], conn)  # site suddenly shows 2 of 5 locations
