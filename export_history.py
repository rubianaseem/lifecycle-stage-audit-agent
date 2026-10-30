#!/usr/bin/env python3
"""Turn HubSpot Lifecycle Stage property history into sample_stage_history.csv format.

Live mode (needs a private app token with crm.objects.contacts.read):
    HUBSPOT_TOKEN=pat-... python export_history.py --out my_history.csv

Offline mode, to see the transform on a saved API response:
    python export_history.py --from-json docs/sample_api_response.json --out /tmp/history.csv

The CRM v3 contacts endpoint returns history when you ask for
propertiesWithHistory=lifecyclestage. Values come newest first, so the
previous (older) entry becomes old_value.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.hubapi.com/crm/v3/objects/contacts"


def fetch_pages(token: str, max_pages: int) -> list[dict]:
    results, after = [], None
    for _ in range(max_pages):
        params = {"limit": 50, "propertiesWithHistory": "lifecyclestage", "archived": "false"}
        if after:
            params["after"] = after
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Bearer {token}"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    page = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
        results.extend(page.get("results", []))
        after = page.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def to_rows(results: list[dict]) -> list[dict]:
    rows = []
    for contact in results:
        history = contact.get("propertiesWithHistory", {}).get("lifecyclestage", [])
        for i, h in enumerate(history):
            older = history[i + 1]["value"] if i + 1 < len(history) else ""
            rows.append({
                "record_id": contact.get("id", ""),
                "timestamp": h.get("timestamp", "").replace("Z", "")[:19],
                "old_value": older,
                "new_value": h.get("value", ""),
                "source_type": h.get("sourceType", ""),
                "source_name": h.get("sourceLabel") or h.get("sourceId") or h.get("updatedByUserId") or "",
            })
    rows.sort(key=lambda r: (r["record_id"], r["timestamp"]))
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--from-json", help="Saved API response instead of calling HubSpot")
    p.add_argument("--out", default="my_history.csv")
    p.add_argument("--max-pages", type=int, default=20)
    a = p.parse_args()
    if a.from_json:
        with open(a.from_json) as f:
            results = json.load(f).get("results", [])
    else:
        token = os.getenv("HUBSPOT_TOKEN")
        if not token:
            print("No HUBSPOT_TOKEN set. Use --from-json for an offline run, or see SETUP.md.")
            return 1
        results = fetch_pages(token, a.max_pages)
    rows = to_rows(results)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["record_id", "timestamp", "old_value", "new_value", "source_type", "source_name"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} history rows for {len(results)} contacts to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
