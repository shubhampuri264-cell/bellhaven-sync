"""Write one approved proposal to the CRM.

This is the only code path that changes CRM data, and it only runs when a
reviewer clicks approve. It checks everything again against the live record
first, because the proposal may be hours old by the time someone reviews it.
"""
from matcher import billing_locked
from normalize import norm_name, norm_street


class StaleProposal(Exception):
    """The CRM record changed since we proposed. Re-run the pipeline."""


class SopViolation(Exception):
    """This write would re-parent a billing-locked account. Never allowed."""


class ApplyError(Exception):
    """The CRM accepted the call but the record does not show the change."""


def _check(crm, op):
    """Drift check and SOP guard for one patch, against the live account."""
    live = crm.get_account(op["account_id"])
    drift = {f: (old, live.get(f)) for f, old in op["expect"].items() if live.get(f) != old}
    if drift:
        raise StaleProposal(f"account {op['account_id']} changed since proposed: {drift}")
    new_parent = op["fields"].get("parent_id")
    if new_parent is not None and new_parent != live["parent_id"] and billing_locked(live):
        raise SopViolation(f"account {op['account_id']} has revenue history and open AR; its parent must not change")
    return live


def _find_existing(crm, fields):
    """Has this account already been created (for example by a half-finished earlier attempt)?"""
    for account in crm.list_accounts(parent_id=fields["parent_id"]):
        if (norm_name(account["name"]) == norm_name(fields["name"])
                and norm_street(account["billing_street"]) == norm_street(fields["billing_street"])
                and not account["duplicate_of_account"]):
            return account
    return None


def _verify(crm, account_id, fields, log):
    """Read the account back and confirm every field we sent is there."""
    saved = crm.get_account(account_id)
    wrong = {f: (v, saved.get(f)) for f, v in fields.items() if saved.get(f) != v}
    if wrong:
        raise ApplyError(f"account {account_id} did not save as sent (sent, got): {wrong}")
    log.append(f"verified {account_id}")
    return saved


def apply_proposal(crm, proposal):
    """Run the proposal's ops in order. Returns {"log", "new_account_id"}."""
    ops = proposal["ops"]
    live = {op["account_id"]: _check(crm, op) for op in ops if op["op"] == "patch"}  # check all before writing any

    log, new_id = [], None
    for op in ops:
        if op["op"] == "create":
            existing = _find_existing(crm, op["fields"])
            if existing:
                new_id = existing["account_id"]
                log.append(f"account already exists, reusing {new_id}")
            else:
                new_id = crm.create_account(op["fields"])["account_id"]
                log.append(f"created {new_id} '{op['fields']['name']}'")
            _verify(crm, new_id, {k: v for k, v in op["fields"].items() if k != "note"}, log)
        else:
            fields = {k: (new_id if v == "$NEW" else v) for k, v in op["fields"].items()}
            if op.get("append_note"):
                old_note = live[op["account_id"]]["note"]
                fields["note"] = f"{old_note}\n{op['append_note']}" if old_note else op["append_note"]
            crm.update_account(op["account_id"], fields)
            log.append(f"patched {op['account_id']}: {fields}")
            _verify(crm, op["account_id"], fields, log)
    return {"log": log, "new_account_id": new_id}
