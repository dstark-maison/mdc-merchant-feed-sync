#!/usr/bin/env python3
"""
Weekly digest email for mdc-merchant-feed-sync, sent via Resend -- same
provider/pattern as cerda-sync's daily report email, but weekly (not daily)
per this pipeline's brief, since a daily feed rebuild doesn't need a daily
inbox interruption the way cerda-sync's price/stock write-backs do.

Reads the last 7 days of reports/YYYY-MM-DD.md (written by build_feed.py's
run_pipeline()), rolls them into one digest, and either prints it (--dry-run,
the default -- and the only mode this repo has ever actually been run in, see
README) or POSTs it to the Resend API if RESEND_API_KEY is set and --dry-run
is not passed.

Usage:
  python send_weekly_report.py                # dry run, prints the digest
  python send_weekly_report.py --send          # actually calls Resend (needs RESEND_API_KEY)
"""
import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    # Report/digest content can contain accented characters (product titles)
    # and the odd emoji marker -- Windows consoles default to cp1252, which
    # can't encode either. GitHub Actions runners are UTF-8 already, so this
    # only matters for local/manual runs, but it's cheap to make robust.
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"

RESEND_FROM = "Merchant Feed Sync Bot <sync@reports.maisondecocon.com>"
RESEND_TO = ["ds@maisondecocon.com"]


def collect_week(end_date=None):
    """Returns [(date, report_text_or_None), ...] for the 7 days ending on
    end_date (inclusive), oldest first. A missing report file for a given
    day means the daily workflow didn't run or didn't commit a report that
    day -- surfaced explicitly in the digest, not silently skipped, so a
    silent pipeline outage is visible in the weekly email rather than just
    producing a shorter-than-expected digest."""
    end_date = end_date or date.today()
    days = [end_date - timedelta(days=i) for i in range(6, -1, -1)]
    results = []
    for day in days:
        path = REPORTS_DIR / f"{day.isoformat()}.md"
        results.append((day, path.read_text(encoding="utf-8") if path.exists() else None))
    return results


def build_digest(week):
    lines = [f"# Weekly merchant feed digest -- {week[0][0].isoformat()} to {week[-1][0].isoformat()}", ""]
    missing = [day for day, text in week if text is None]
    present = [(day, text) for day, text in week if text is not None]

    lines.append(f"Runs found: {len(present)} / 7")
    if missing:
        lines.append(f"Missing runs (no report committed that day): {', '.join(d.isoformat() for d in missing)}")
    lines.append("")

    total_accepted = total_excluded = total_sample_rejected = 0
    for day, text in present:
        for line in text.splitlines():
            if line.startswith("- Accepted into feed:"):
                total_accepted += int(line.rsplit(":", 1)[1].strip())
            elif line.startswith("- Excluded (validation failures):"):
                total_excluded += int(line.rsplit(":", 1)[1].strip())
            elif line.startswith("- Rejected (known Google sample/placeholder data):"):
                total_sample_rejected += int(line.rsplit(":", 1)[1].strip())

    lines.append("## Totals across the week")
    lines.append(f"- Accepted into feed (sum across runs): {total_accepted}")
    lines.append(f"- Validation exclusions (sum across runs): {total_excluded}")
    lines.append(f"- Sample-data rejects (sum across runs): {total_sample_rejected}")
    lines.append("")

    if total_sample_rejected:
        lines.append(
            "⚠️ At least one run this week rejected rows matching known Google "
            "sample/placeholder data -- check the daily reports below for which SKUs, "
            "this is the exact failure mode that caused the original suspension."
        )
        lines.append("")

    lines.append("## Daily reports")
    for day, text in present:
        lines.append(f"### {day.isoformat()}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def send_via_resend(subject, body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("WARNING: RESEND_API_KEY not set -- cannot send, falling back to printing the digest.")
        print(body)
        return
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": RESEND_FROM, "to": RESEND_TO, "subject": subject, "text": body},
        timeout=30,
    )
    print(resp.status_code, resp.text)
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="Actually POST to Resend instead of printing (default: dry-run/print only)")
    args = parser.parse_args()

    week = collect_week()
    digest = build_digest(week)
    subject = f"Merchant feed weekly digest -- {week[-1][0].isoformat()}"

    if args.send:
        send_via_resend(subject, digest)
    else:
        print(f"[dry-run] Subject: {subject}\n")
        print(digest)


if __name__ == "__main__":
    main()
