"""Match website locations to CRM accounts and decide what should change.

Pure logic: takes lists in, returns a plan. It never calls the CRM, so it can be
tested with made-up data and it can never write anything by accident.

Every proposal carries:
  changes   what the reviewer sees (field, from, to)
  evidence  why we think so, in plain sentences
  ops       the exact API calls to make if the reviewer approves
"""
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date

import config
from normalize import (is_po_box, name_similarity, norm_name, norm_phone,
                       norm_street, norm_text, norm_zip)

# Fields we snapshot when proposing. If any of them changed in the CRM by the
# time the reviewer clicks approve, the proposal is stale and nothing is written.
WATCHED_FIELDS = ["name", "parent_id", "billing_street", "billing_city", "billing_state",
                  "billing_zip", "care_type", "status", "duplicate_of_account", "chow_current_account"]

FIELD_LABELS = {"name": "name", "billing_zip": "zip", "billing_city": "city",
                "billing_street": "street", "status": "status", "care_type": "care type",
                "parent_id": "parent"}


class MatchError(Exception):
    pass


@dataclass
class Proposal:
    key: str           # stable id, used to remember decisions across runs
    action: str        # update | reparent | chow | chow_link | duplicate | create | needs_review
    title: str
    account_id: str    # account being changed ("" when creating)
    location_slug: str # website location ("" for accounts not on the website)
    confidence: str    # high | medium | low
    changes: list
    evidence: list
    ops: list

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- helpers

def billing_locked(account):
    """The SOP test: revenue history AND open AR means the parent must not change."""
    return account["lifetime_revenue"] > 0 and account["outstanding_ar"] > 0


def sop_line(account):
    money = f"lifetime revenue ${account['lifetime_revenue']:,.0f}, outstanding AR ${account['outstanding_ar']:,.0f}"
    if billing_locked(account):
        return f"SOP check: {money}. Revenue history AND open AR, so this account must stay as it is for billing."
    return f"SOP check: {money}. Not both revenue history and open AR, so the existing account can be changed directly."


def make_key(action, target, key_fields):
    """Same decision on the same record always gets the same key. Notes and dates
    are left out so tomorrow's run produces the same key as today's."""
    digest = hashlib.sha1(json.dumps([action, target, key_fields], sort_keys=True).encode()).hexdigest()[:10]
    return f"{action}-{target}-{digest}"


def snapshot(account):
    return {f: account[f] for f in WATCHED_FIELDS}


def find_operator(accounts):
    """The operator's parent account: named like the operator, with no parent of its own.
    If more than one fits, the one that other accounts point to wins."""
    parent_ids = {a["parent_id"] for a in accounts if a["parent_id"]}
    wanted = norm_name(config.OPERATOR_NAME)
    hits = [a for a in accounts if not a["parent_id"] and norm_name(a["name"]).startswith(wanted)]
    if len(hits) > 1:
        hits = [a for a in hits if a["account_id"] in parent_ids]
    if len(hits) != 1:
        raise MatchError(f"expected exactly one parent account for {config.OPERATOR_NAME}, found {len(hits)}")
    return hits[0]


def compare(loc, account, contact_names):
    """Every signal we use to decide if a location and an account are the same facility."""
    return {
        "state": loc.state.upper() == account["billing_state"].upper(),
        "city": norm_text(loc.city) == norm_text(account["billing_city"]),
        "zip": norm_zip(loc.zip) == norm_zip(account["billing_zip"]),
        "street": bool(account["billing_street"]) and norm_street(loc.street) == norm_street(account["billing_street"]),
        "street_raw": loc.street.strip().lower() == account["billing_street"].strip().lower(),
        "name_sim": round(name_similarity(loc.name, account["name"]), 2),
        "phone": bool(norm_phone(loc.phone)) and norm_phone(loc.phone) == norm_phone(account["phone"]),
        "admin": bool(loc.administrator) and norm_text(loc.administrator) in contact_names,
    }


def is_candidate(signals):
    """Same state is required. Then either the same street in the same city or zip,
    or nearly the same name in the same city (covers a PO Box on the CRM side)."""
    if not signals["state"]:
        return False
    same_place = signals["street"] and (signals["city"] or signals["zip"])
    same_name = signals["name_sim"] >= config.NAME_MATCH_THRESHOLD and signals["city"]
    return bool(same_place or same_name)


def survivor_rank(account, signals, operator_id):
    """When several accounts match one location, the highest tuple is kept.
    Order matters: already under the operator, then billing history, then how
    many hard signals agree, then having a parent at all, then name closeness."""
    hard_signals = signals["phone"] + signals["admin"] + signals["street_raw"]
    return (account["parent_id"] == operator_id, account["lifetime_revenue"] > 0,
            hard_signals, bool(account["parent_id"]), signals["name_sim"])


def match_evidence(loc, account, signals):
    lines = []
    if signals["street"]:
        lines.append(f"Street matches: website '{loc.street}', CRM '{account['billing_street']}'.")
    else:
        lines.append(f"Street differs: website '{loc.street}', CRM '{account['billing_street']}'.")
    place = [label for label, ok in (("city", signals["city"]), ("state", signals["state"]), ("zip", signals["zip"])) if ok]
    lines.append(f"Same {', '.join(place)}." if place else "City, state and zip all differ.")
    lines.append(f"Name similarity {signals['name_sim']:.2f}: website '{loc.name}', CRM '{account['name']}'.")
    if signals["phone"]:
        lines.append(f"Phone matches the website: {loc.phone}.")
    if signals["admin"]:
        lines.append(f"Website administrator {loc.administrator} is a contact on this CRM account.")
    return lines


def care_types(loc):
    return [config.CARE_TYPE_MAP[o] for o in loc.care_offerings if o in config.CARE_TYPE_MAP]


def new_account_fields(loc, operator, note):
    mapped = care_types(loc)
    return {"name": loc.name, "parent_id": operator["account_id"], "billing_street": loc.street,
            "billing_city": loc.city, "billing_state": loc.state, "billing_zip": loc.zip,
            "care_type": mapped[0] if mapped else "", "status": "Active", "phone": loc.phone, "note": note}


# ---------------------------------------------------------------- proposal builders

def propose_survivor(loc, account, signals, operator, confidence, evidence, today):
    """The account we keep for this location. Returns a proposal, or None if it is already right."""
    evidence = list(evidence)
    fields = {}
    if account["name"] != loc.name:
        fields["name"] = loc.name
        if norm_name(account["name"]) == norm_name(loc.name):
            evidence.append("Name differs only in spelling or punctuation. Website spelling proposed.")
        else:
            evidence.append("CRM name is outdated. The website now lists this address under the new name.")
    if not signals["zip"]:
        fields["billing_zip"] = loc.zip
    if not signals["city"]:
        fields["billing_city"] = loc.city
    if not signals["street"]:
        if is_po_box(account["billing_street"]):
            evidence.append("CRM street is a PO Box (a mailing address). Left as is.")
        else:
            fields["billing_street"] = loc.street
    if account["status"] != "Active":
        fields["status"] = "Active"
    mapped = care_types(loc)
    if mapped and account["care_type"] not in mapped:
        fields["care_type"] = mapped[0]

    if account["parent_id"] != operator["account_id"]:
        old_parent = account["parent_name"] or "no parent"
        evidence.append(f"Listed on the {config.OPERATOR_NAME} website but the CRM parent is {old_parent}.")
        evidence.append(sop_line(account))
        if billing_locked(account):
            return propose_chow(loc, account, operator, confidence, evidence, today)
        fields["parent_id"] = operator["account_id"]

    if not fields:
        return None

    changes = []
    for name, new_value in fields.items():
        if name == "parent_id":
            changes.append({"field": "parent", "from": account["parent_name"] or "(none)", "to": operator["name"]})
        else:
            changes.append({"field": FIELD_LABELS[name], "from": account[name], "to": new_value})
    action = "reparent" if "parent_id" in fields else "update"
    if action == "reparent":
        title = f"Move '{account['name']}' under {config.OPERATOR_NAME}"
        if "name" in fields:
            title += f" and rename it '{loc.name}'"
    else:
        title = f"Fix {', '.join(FIELD_LABELS[f] for f in fields)} on '{account['name']}'"
    ops = [{"op": "patch", "account_id": account["account_id"], "fields": fields, "expect": snapshot(account)}]
    return Proposal(make_key(action, account["account_id"], fields), action, title,
                    account["account_id"], loc.slug, confidence, changes, evidence, ops)


def propose_chow(loc, account, operator, confidence, evidence, today):
    """Change of ownership on a billing-locked account: leave it alone, create a
    new account under the right parent, point the old one at the new one."""
    note = (f"Created {today} by bellhaven-sync after a change of ownership. Earlier account "
            f"{account['account_id']} stays under {account['parent_name'] or 'no parent'} for billing.")
    fields = new_account_fields(loc, operator, note)
    changes = [
        {"field": "new account", "from": "", "to": f"{loc.name} under {operator['name']}"},
        {"field": "chow_current_account (old account)", "from": account["chow_current_account"], "to": "id of the new account"},
    ]
    ops = [
        {"op": "create", "fields": fields},
        {"op": "patch", "account_id": account["account_id"], "fields": {"chow_current_account": "$NEW"},
         "expect": snapshot(account)},
    ]
    key = make_key("chow", account["account_id"], {"slug": loc.slug, "name": loc.name})
    title = f"Change of ownership: new account for '{loc.name}', old one kept for billing"
    return Proposal(key, "chow", title, account["account_id"], loc.slug, confidence, changes, evidence, ops)


def propose_retire(account, keep, slug, confidence, evidence, today):
    """`account` is an extra record for a facility whose real account is `keep`.
    Normally that is a duplicate. If billing needs it, link it instead of closing it."""
    evidence = list(evidence)
    if billing_locked(account):
        evidence.append(sop_line(account))
        fields = {"chow_current_account": keep["account_id"]}
        changes = [{"field": "chow_current_account", "from": "", "to": f"{keep['name']} ({keep['account_id']})"}]
        ops = [{"op": "patch", "account_id": account["account_id"], "fields": fields, "expect": snapshot(account)}]
        title = f"Link '{account['name']}' to its current account '{keep['name']}', keep it for billing"
        return Proposal(make_key("chow_link", account["account_id"], fields), "chow_link", title,
                        account["account_id"], slug, confidence, changes, evidence, ops)

    fields = {"duplicate_of_account": keep["account_id"], "status": "Inactive"}
    note = (f"Duplicate of {keep['name']} ({keep['account_id']}), same facility at {account['billing_street']}. "
            f"Marked {today} by bellhaven-sync.")
    changes = [
        {"field": "duplicate_of_account", "from": "", "to": f"{keep['name']} ({keep['account_id']})"},
        {"field": "status", "from": account["status"], "to": "Inactive"},
        {"field": "note", "from": account["note"], "to": note},
    ]
    ops = [{"op": "patch", "account_id": account["account_id"], "fields": fields, "append_note": note,
            "expect": snapshot(account)}]
    title = f"Mark '{account['name']}' ({account['account_id']}) as a duplicate of '{keep['name']}' ({keep['account_id']})"
    return Proposal(make_key("duplicate", account["account_id"], fields), "duplicate", title,
                    account["account_id"], slug, confidence, changes, evidence, ops)


def propose_create(loc, operator, open_accounts, contact_names, today):
    """No CRM account for this location. List the near misses so the reviewer can see they were checked."""
    evidence = [f"No CRM account at '{loc.street}' in {loc.city}, {loc.state} and no close name match in that city."]
    near = 0
    for account in open_accounts:
        signals = compare(loc, account, contact_names[account["account_id"]])
        same_town = signals["state"] and (signals["city"] or signals["zip"])
        if same_town or signals["name_sim"] >= config.NAME_MATCH_THRESHOLD:
            near += 1
            where = f"{account['billing_street']}, {account['billing_city']}, {account['billing_state']}"
            evidence.append(f"Checked and rejected: '{account['name']}' ({where}, parent {account['parent_name'] or 'none'}). "
                            f"Street match {signals['street']}, same state {signals['state']}, "
                            f"name similarity {signals['name_sim']:.2f}, phone match {signals['phone']}.")
    note = f"Created {today} by bellhaven-sync from the website listing {loc.url}."
    fields = new_account_fields(loc, operator, note)
    changes = [{"field": "new account", "from": "", "to": f"{loc.name}, {loc.street}, {loc.city}, {loc.state} {loc.zip}"},
               {"field": "parent", "from": "", "to": operator["name"]}]
    ops = [{"op": "create", "fields": fields}]
    key = make_key("create", loc.slug, {"name": loc.name, "street": norm_street(loc.street)})
    return Proposal(key, "create", f"Create account for '{loc.name}'", "", loc.slug,
                    "medium" if near else "high", changes, evidence, ops)


def propose_left(account, open_accounts, operator, today):
    """An active account under the operator that the website no longer lists."""
    street = norm_street(account["billing_street"])
    successors = [o for o in open_accounts
                  if o["account_id"] != account["account_id"] and o["parent_id"] != operator["account_id"]
                  and street and norm_street(o["billing_street"]) == street
                  and o["billing_state"] == account["billing_state"]
                  and norm_text(o["billing_city"]) == norm_text(account["billing_city"])]
    evidence = [f"Parent is {operator['name']} but no page on the website matches this account."]
    if successors:
        keep = max(successors, key=lambda o: (bool(o["parent_id"]), o["lifetime_revenue"]))
        evidence.append(f"Another account sits at the same address: '{keep['name']}' under "
                        f"{keep['parent_name'] or 'no parent'}. That looks like the new owner.")
        return propose_retire(account, keep, "", "high", evidence, today)

    evidence.append("No other account at this address, so the new owner is unknown. Parent left unchanged.")
    fields = {"status": "Needs Review"}
    note = (f"Not listed on the {config.OPERATOR_NAME} website as of {today}. May have been sold or closed. "
            f"Parent left unchanged until ownership is confirmed. Flagged by bellhaven-sync.")
    changes = [{"field": "status", "from": account["status"], "to": "Needs Review"},
               {"field": "note", "from": account["note"], "to": note}]
    ops = [{"op": "patch", "account_id": account["account_id"], "fields": fields, "append_note": note,
            "expect": snapshot(account)}]
    title = f"Flag '{account['name']}': no longer on the website"
    return Proposal(make_key("needs_review", account["account_id"], fields), "needs_review", title,
                    account["account_id"], "", "medium", changes, evidence, ops)


# ---------------------------------------------------------------- main entry

def build_plan(locations, accounts, contacts, today=None):
    """Returns {"operator", "matches", "proposals"}.

    matches   one row per website location: what it matched and how sure we are
    proposals every change we want a reviewer to approve
    """
    today = today or date.today().isoformat()
    operator = find_operator(accounts)
    operator_id = operator["account_id"]
    parent_ids = {a["parent_id"] for a in accounts if a["parent_id"]} | {operator_id}

    contact_names = defaultdict(set)
    for contact in contacts:
        if contact.get("is_active", True):
            contact_names[contact["account_id"]].add(norm_text(contact["name"]))

    # Facility accounts still in play. Parent accounts are not facilities, and an
    # account already marked duplicate or CHOW has been dealt with.
    open_accounts = [a for a in accounts if a["account_id"] not in parent_ids
                     and not a["duplicate_of_account"] and not a["chow_current_account"]]

    matches, proposals, claimed = [], [], set()

    for loc in locations:
        candidates = []
        for account in open_accounts:
            signals = compare(loc, account, contact_names[account["account_id"]])
            if is_candidate(signals):
                candidates.append((survivor_rank(account, signals, operator_id), account, signals))
        candidates.sort(key=lambda c: c[0], reverse=True)

        row = {"slug": loc.slug, "name": loc.name, "address": f"{loc.street}, {loc.city}, {loc.state} {loc.zip}",
               "account_id": "", "account_name": "", "confidence": "", "outcome": "", "proposal_keys": []}

        if not candidates:
            proposal = propose_create(loc, operator, open_accounts, contact_names, today)
            proposals.append(proposal)
            row.update(outcome="no CRM account", confidence=proposal.confidence, proposal_keys=[proposal.key])
            matches.append(row)
            continue

        rank, account, signals = candidates[0]
        # Ambiguous: the top two tie on parent, billing history and hard signals.
        ambiguous = len(candidates) > 1 and candidates[1][0][:3] == rank[:3]
        corroborated = signals["phone"] or signals["admin"] or signals["name_sim"] >= config.NAME_MATCH_THRESHOLD
        if ambiguous:
            confidence = "low"
        elif corroborated and (signals["street"] or signals["zip"]):
            confidence = "high"
        else:
            confidence = "medium"

        evidence = match_evidence(loc, account, signals)
        if len(candidates) > 1:
            others = ", ".join(f"'{c[1]['name']}'" for c in candidates[1:])
            evidence.append(f"{len(candidates)} CRM accounts match this location. Kept this one over {others}.")
        if ambiguous:
            evidence.append("None of them stands out (same parent status, billing history and hard signals). "
                            "Tie broken by: has a parent account, then closest name. Please check.")
        if account["account_id"] in claimed:
            confidence = "low"
            evidence.append("Warning: this account also matched another website location.")

        claimed.add(account["account_id"])
        survivor = propose_survivor(loc, account, signals, operator, confidence, evidence, today)
        keys = []
        if survivor:
            proposals.append(survivor)
            keys.append(survivor.key)

        for _, extra, extra_signals in candidates[1:]:
            claimed.add(extra["account_id"])
            if survivor and survivor.action == "chow":
                continue  # the kept account does not exist yet; the next run picks these up
            why = match_evidence(loc, extra, extra_signals)
            why.append(f"Kept '{account['name']}' ({account['account_id']}) as the account for this facility.")
            retire = propose_retire(extra, account, loc.slug, confidence, why, today)
            proposals.append(retire)
            keys.append(retire.key)

        row.update(account_id=account["account_id"], account_name=account["name"], confidence=confidence,
                   outcome=survivor.action if survivor else "match, no change", proposal_keys=keys)
        matches.append(row)

    for account in open_accounts:
        if account["parent_id"] == operator_id and account["account_id"] not in claimed and account["status"] == "Active":
            proposals.append(propose_left(account, open_accounts, operator, today))

    return {"operator": operator, "matches": matches, "proposals": proposals}
