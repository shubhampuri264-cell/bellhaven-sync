"""An in-memory stand-in for the CRM API, plus small builders for test data.

Tests never touch the real CRM. FakeCRM has the same methods as crm.CRMClient.
"""
import copy

from scraper import Location

OPERATOR = "OP"  # account_id of the Bellhaven parent account in tests


def account(account_id, name, street, city="Akron", state="OH", zip_code="44313",
            parent=OPERATOR, revenue=0, ar=0, **extra):
    parent_names = {OPERATOR: "Bellhaven Senior Living (Parent Account)", "CEDAR": "Cedar Trail (Parent Account)", "": ""}
    row = {"account_id": account_id, "name": name, "parent_id": parent, "parent_name": parent_names[parent],
           "billing_street": street, "billing_city": city, "billing_state": state, "billing_zip": zip_code,
           "care_type": "Assisted Living", "status": "Active", "phone": "", "lifetime_revenue": revenue,
           "outstanding_ar": ar, "chow_current_account": "", "duplicate_of_account": "", "note": ""}
    row.update(extra)
    return row


def parents():
    """The two parent accounts every test world starts with."""
    return [account(OPERATOR, "Bellhaven Senior Living (Parent Account)", "", "", "", "", parent="", care_type=""),
            account("CEDAR", "Cedar Trail (Parent Account)", "", "", "", "", parent="", care_type="")]


def location(name, street, city="Akron", state="OH", zip_code="44313", **extra):
    slug = name.lower().replace(" ", "-")
    fields = {"slug": slug, "url": f"https://example.test/communities/{slug}", "name": name, "street": street,
              "city": city, "state": state, "zip": zip_code, "care_offerings": ["Assisted Living"]}
    fields.update(extra)
    return Location(**fields)


class FakeCRM:
    def __init__(self, accounts, contacts=()):
        self.accounts = {a["account_id"]: copy.deepcopy(a) for a in accounts}
        self.contacts = list(contacts)
        self.writes = []  # every create and patch, in order

    def list_accounts(self, **filters):
        rows = [copy.deepcopy(a) for a in self.accounts.values()]
        if "parent_id" in filters:
            rows = [a for a in rows if a["parent_id"] == filters["parent_id"]]
        return rows

    def list_contacts(self, **filters):
        return list(self.contacts)

    def get_account(self, account_id):
        return copy.deepcopy(self.accounts[account_id])

    def update_account(self, account_id, fields):
        self.writes.append(("patch", account_id, dict(fields)))
        self.accounts[account_id].update(fields)
        if "parent_id" in fields:
            self.accounts[account_id]["parent_name"] = self.accounts[fields["parent_id"]]["name"]
        return self.get_account(account_id)

    def create_account(self, fields):
        account_id = f"NEW{len(self.accounts)}"
        self.writes.append(("create", account_id, dict(fields)))
        row = account(account_id, fields["name"], fields["billing_street"], parent="")
        row.update(fields)
        row["parent_name"] = self.accounts[fields["parent_id"]]["name"]
        self.accounts[account_id] = row
        return self.get_account(account_id)
