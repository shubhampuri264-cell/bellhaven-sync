# bellhaven-sync

Keeps the CRM's picture of Bellhaven Senior Living in line with the operator's own website.

Each day it scrapes every community from the website, matches each one to a CRM account, and queues the differences as proposals. A reviewer reads each proposal with its evidence and approves or rejects it. Only an approval writes to the CRM.

## Run it

```
python -m venv .venv
.venv\Scripts\activate            (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            then paste your CRM token into .env

python pipeline.py --dry-run      print proposals, save nothing
python pipeline.py                scrape, match, fill the review queue
python app.py                     review app at http://127.0.0.1:5000
pytest                            34 tests, no network needed
```

## How it is put together

| File | Job |
|---|---|
| `scraper.py` | Pulls every location from the website |
| `crm.py` | The only file that calls the CRM API |
| `normalize.py` | Cleans up streets, names, phones so they can be compared |
| `matcher.py` | Matches locations to accounts and builds proposals. Pure logic, no network |
| `store.py` | SQLite review queue and the memory of past decisions |
| `pipeline.py` | The daily job. Read-only against the CRM |
| `apply.py` | Writes one approved proposal, after re-checking the live record |
| `app.py` | The review app |
| `config.py` | Every setting in one place |

There is no LLM call anywhere in the pipeline. Matching is rule based, so the same input always gives the same output and every decision can be explained line by line.

## Scraping

The directory says "34 communities listed" but the home page says 35. One community is only linked from the home page banner. The scraper collects community links from every page it visits, not just the directory, and found all 35.

Asking the directory for a page past the end returns the last page again, so the scraper follows the "Next" link and stops when it is gone. If it ever scrapes fewer locations than the directory claims, or far fewer than the previous run, it stops instead of flagging accounts as gone.

## Matching

An account is a candidate for a location when the state matches and either:

1. the street matches after cleanup ("4850 Northwest Sylvania Avenue" equals "4850 NW Sylvania Ave") in the same city or zip, or
2. the name is nearly identical in the same city. This covers an account whose CRM street is a PO Box.

Phone, administrator (website) against contacts (CRM), and name similarity are supporting evidence. They raise or lower confidence but never create a match on their own. That is what keeps look-alikes out: "Amberly Manor" in Colorado, "Union Square Senior Living" on a different street, "New Carlisle, OH" against "Carlisle, PA".

When several accounts match one location, one is kept and the rest are retired. The kept one is chosen in this order: already under Bellhaven, has billing history, most hard signals (phone, administrator, exact street), has a parent account, closest name. If the top two tie on the first three, the proposals are marked low confidence so the reviewer looks harder.

## What it proposes

| Situation | Proposal |
|---|---|
| Location matches an account and nothing differs | Nothing. Shown on the "all website locations" page |
| Matched, but name or zip is wrong | Update the field |
| Matched, wrong parent, no billing lock | Re-parent the existing account |
| Matched, wrong parent, revenue history and open AR | Change of ownership: create a new account under Bellhaven, set `chow_current_account` on the old one, change nothing else on it |
| More than one account for the same facility | Extra copies get `duplicate_of_account` and status Inactive |
| Location has no account | Create one under Bellhaven |
| Account under Bellhaven, not on the website, another owner's account at the same address | Link the old account to the new owner's account with `chow_current_account` if billing locked, otherwise mark it a duplicate of that account |
| Account under Bellhaven, not on the website, no other account at the address | Status Needs Review plus a note. Parent left alone because the new owner is unknown |

Phone numbers are never overwritten. The task asks for name, address and care offerings, and a CRM phone may be a sales contact line rather than the front desk.

A PO Box street is never overwritten either. It is a mailing address, not a wrong address.

## The billing SOP

The rule is checked twice.

1. `matcher.py` decides between "re-parent" and "change of ownership" from `lifetime_revenue` and `outstanding_ar`.
2. `apply.py` checks the live account again at write time and refuses any parent change on a billing locked account, no matter what the proposal says.

The second check exists because a proposal can sit in the queue for hours and AR can change in that time.

## Safe re-runs

Every proposal gets a key built from the action, the record, and the new values. Dates and note text are left out of the key so tomorrow's run produces the same key as today's.

* Approved or rejected keys are never proposed again.
* Pending keys are refreshed in place, not duplicated.
* A pending proposal that the latest run no longer produces is marked obsolete.
* Accounts already marked duplicate or CHOW are skipped by the matcher.

Before any write, `apply.py` compares the live account with the snapshot taken when the proposal was made. If the record changed, the proposal is marked stale and nothing is written. After every write it reads the account back and confirms the change landed. Creating an account first checks whether it already exists, so a retry after a half finished change of ownership does not create a second account.

## Schedule

`schedule/crontab.txt` runs `pipeline.py` daily at 06:15. The job only fills the review queue. It exits non-zero on failure so the scheduler can alert.

I chose cron on one machine over GitHub Actions because the decision history lives in a local SQLite file, and an Actions runner starts empty every time. Moving that table to a hosted database is the first change I would make before running this on shared infrastructure.

## Tests

`pytest` runs 34 tests against an in-memory fake CRM. They cover each row of the table above, the look-alike cases, the SOP guard, stale proposals, retry after a partial failure, and the re-run promises.

## What I would build next

* Move contacts from a duplicate to the surviving account.
* Let the reviewer pick the surviving account when the choice is a tie, instead of only approving or rejecting the pipeline's pick.
* Hosted database and login for the review app so more than one reviewer can use it.
* An alert when a run fails or when the queue has waited more than a day.
* Run it for every operator, not just one, with the operator list in config.
