"""Daily job: scrape the website, read the CRM, queue proposals for review.

Read-only against the CRM. The only thing it writes is the local review queue.

    python pipeline.py            run and save proposals
    python pipeline.py --dry-run  print what it would propose, save nothing
"""
import argparse
import sys

import config
import store
from crm import CRMClient
from matcher import build_plan
from scraper import scrape_locations


class PipelineError(Exception):
    pass


def run(crm=None, conn=None, locations=None, dry_run=False):
    """Returns (plan, stats). `locations` can be passed in by tests to skip scraping."""
    crm = crm or CRMClient()
    conn = conn or store.connect()

    if locations is None:
        locations, _ = scrape_locations()

    # Safety: if the site returned far fewer locations than last time, the site is
    # probably broken. Stop before we flag half the CRM as "left the operator".
    previous = store.last_run(conn)
    if previous and len(locations) < previous["locations"] * config.MIN_SCRAPE_RATIO:
        raise PipelineError(f"scraped {len(locations)} locations but the last run had {previous['locations']}; "
                            "refusing to continue")

    accounts = crm.list_accounts()
    contacts = crm.list_contacts()
    plan = build_plan(locations, accounts, contacts)

    if dry_run:
        return plan, {"generated": len(plan["proposals"])}
    stats = store.record_run(conn, plan["proposals"], plan["matches"], len(locations), len(accounts))
    return plan, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print proposals, save nothing")
    args = parser.parse_args()

    try:
        plan, stats = run(dry_run=args.dry_run)
    except Exception as error:  # a scheduled job must exit non-zero so the failure is noticed
        print(f"PIPELINE FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"{store.now()} locations={len(plan['matches'])} " + " ".join(f"{k}={v}" for k, v in stats.items()))
    if args.dry_run:
        for p in plan["proposals"]:
            print(f"  [{p.confidence:6}] {p.action:12} {p.title}")


if __name__ == "__main__":
    main()
