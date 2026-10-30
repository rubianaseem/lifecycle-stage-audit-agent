#!/usr/bin/env python3
"""Lifecycle Stage Audit Agent.

Audits a HubSpot contact export plus Lifecycle Stage property history.
Finds custom values in the native property, stages that contradict deal
evidence, leads stuck because no workflow moves them, backward moves and
workflows that fight over the same property. Writes a safe fix file, a
review queue, a workflow-writer report and a brief.

Runs on rules with no API key. With ANTHROPIC_API_KEY set, Claude writes
the brief's summary and the review notes from the findings only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional
    pass

HERE = Path(__file__).parent

# HubSpot shows labels in exports and internal names in the API. Both are fine.
LABELS = {
    "subscriber": "subscriber",
    "lead": "lead",
    "marketing qualified lead": "marketingqualifiedlead",
    "sales qualified lead": "salesqualifiedlead",
    "opportunity": "opportunity",
    "customer": "customer",
    "evangelist": "evangelist",
    "other": "other",
}

DISPLAY = {v: k.title() for k, v in LABELS.items()}
DISPLAY["marketingqualifiedlead"] = "MQL"
DISPLAY["salesqualifiedlead"] = "SQL"


def display(stage: str | None, raw: str = "") -> str:
    if stage:
        return DISPLAY.get(stage, stage)
    raw = " ".join(raw.split())
    return f"{raw} (custom)" if raw else "(blank)"


# Raw property-history source types mapped to plain names.
SOURCE_TYPES = {
    "AUTOMATION_PLATFORM": "WORKFLOW",
    "WORKFLOW": "WORKFLOW",
    "CRM_UI": "USER",
    "USER": "USER",
    "IMPORT": "IMPORT",
    "FORM": "FORM",
    "INTEGRATION": "INTEGRATION",
    "API": "INTEGRATION",
    "DEAL_AUTOMATION": "DEAL_AUTOMATION",
}


@dataclass
class Finding:
    record_id: str
    email: str
    code: str
    severity: str  # high, medium, low
    evidence: str
    fix_stage: str = ""
    fix_lead_status: str = ""
    confidence: str = "review"  # safe or review
    clear_first: bool = False
    review_note: str = ""


@dataclass
class Contact:
    row: dict
    record_id: str
    email: str
    raw_stage: str
    stage: str | None  # native internal name, or None if custom/blank
    custom_rule: dict | None
    score: float | None
    deals: list[str]
    findings: list[Finding] = field(default_factory=list)


# ---------- loading and normalising ----------

def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def parse_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            continue
    return None


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def normalise_stage(raw: str, policy: dict) -> tuple[str | None, dict | None]:
    """Return (native internal name, custom rule). Blank returns (None, None)."""
    key = " ".join(raw.split()).lower()
    if not key:
        return None, None
    native = set(policy["native_order"]) | set(policy.get("unordered_native", []))
    if key in native:
        return key, None
    if key in LABELS:
        return LABELS[key], None
    if key in policy.get("aliases", {}):
        return policy["aliases"][key], None
    if key in policy.get("custom_options", {}):
        return None, policy["custom_options"][key]
    return None, {"label": raw, "action": "review",
                  "note": "Unknown value in the native property. Find out who added it before mapping it."}


def rank(stage: str | None, policy: dict) -> int:
    order = policy["native_order"]
    return order.index(stage) if stage in order else -1


def build_contacts(rows: list[dict], policy: dict) -> list[Contact]:
    contacts = []
    for r in rows:
        stage, rule = normalise_stage(r.get("lifecycle_stage", ""), policy)
        try:
            score = float(r["hubspot_score"]) if r.get("hubspot_score") else None
        except ValueError:
            score = None
        deals = [d.strip().lower() for d in r.get("deal_stages", "").replace(",", ";").split(";") if d.strip()]
        contacts.append(Contact(r, r.get("record_id", ""), r.get("email", "").strip().lower(),
                                r.get("lifecycle_stage", ""), stage, rule, score, deals))
    return contacts


# ---------- contact-level checks ----------

def check_value(c: Contact, policy: dict) -> None:
    if c.stage is None and c.custom_rule is None:
        c.findings.append(Finding(c.record_id, c.email, "MISSING_STAGE", "medium",
                                  "Lifecycle Stage is blank, so this contact is invisible in the funnel report.",
                                  fix_stage="lead", confidence="safe"))
        c.stage = "lead"  # assume Lead for the evidence checks that follow
        return
    rule = c.custom_rule
    if rule is None:
        return
    label = rule.get("label", c.raw_stage)
    numeric = c.raw_stage.strip().isdigit()
    evidence = f"Value '{c.raw_stage.strip()}' is a custom option ('{label}') inside the native Lifecycle Stage property."
    if numeric:
        evidence += " Its internal name is a number, which the funnel report treats as a separate stage."
    if rule.get("action") == "move_to_lead_status":
        c.findings.append(Finding(c.record_id, c.email, "NON_NATIVE_VALUE", "high", evidence,
                                  fix_stage=rule["stage"], fix_lead_status=rule.get("lead_status", ""),
                                  confidence="safe", clear_first=True))
        c.stage = rule["stage"]
    else:
        c.findings.append(Finding(c.record_id, c.email, "NON_NATIVE_VALUE", "high", evidence,
                                  review_note=rule.get("note", "")))


def check_deal_evidence(c: Contact, policy: dict) -> None:
    won = set(policy["closed_won_stages"])
    lost = set(policy["closed_lost_stages"])
    has_won = any(d in won for d in c.deals)
    has_open = any(d not in won and d not in lost for d in c.deals)
    r = rank(c.stage, policy)
    if has_won and 0 <= r < rank("customer", policy):
        c.findings.append(Finding(c.record_id, c.email, "STAGE_BEHIND_DEAL", "high",
                                  f"Has a closed-won deal but sits at '{c.stage}'.",
                                  fix_stage="customer", confidence="safe"))
    elif has_open and 0 <= r < rank("opportunity", policy):
        c.findings.append(Finding(c.record_id, c.email, "STAGE_BEHIND_DEAL", "medium",
                                  f"Has an open deal ({', '.join(c.deals)}) but sits at '{c.stage}'. "
                                  "HubSpot's default deal automation may be switched off.",
                                  fix_stage="opportunity", confidence="safe"))
    if c.stage == "customer" and not has_won:
        c.findings.append(Finding(c.record_id, c.email, "CUSTOMER_WITHOUT_WON_DEAL", "high",
                                  "Marked Customer with no closed-won deal"
                                  + (f" (deals: {', '.join(c.deals)})." if c.deals else "."),
                                  review_note="Check billing or contract records before changing it. "
                                              "Legacy imports often set Customer by hand."))


def check_stuck(c: Contact, policy: dict, as_of: date) -> None:
    thr = policy["mql_score_threshold"]
    if c.stage == "lead" and c.score is not None and c.score >= thr and not c.custom_rule:
        entered = parse_date(c.row.get("date_entered_lead", "")) or parse_date(c.row.get("create_date", ""))
        days = (as_of - entered).days if entered else None
        if days is not None and days > policy["stuck_lead_days"]:
            c.findings.append(Finding(c.record_id, c.email, "STUCK_AT_LEAD", "medium",
                                      f"Score {c.score:.0f} is over the MQL threshold of {thr}, "
                                      f"but the contact has sat at Lead for {days} days.",
                                      fix_stage="marketingqualifiedlead", confidence="safe",
                                      review_note="Also check why the MQL workflow did not enrol this contact."))
    if c.stage == "marketingqualifiedlead" and not c.row.get("owner"):
        entered = parse_date(c.row.get("date_entered_mql", ""))
        days = (as_of - entered).days if entered else None
        if days is None or days > policy["mql_owner_sla_days"]:
            c.findings.append(Finding(c.record_id, c.email, "MQL_NO_OWNER", "high",
                                      f"MQL for {days if days is not None else 'an unknown number of'} days with no contact owner, "
                                      "so nobody in sales has been told.",
                                      review_note="Fix routing, not the stage."))


DATE_FIELDS = [("lead", "date_entered_lead"), ("marketingqualifiedlead", "date_entered_mql"),
               ("salesqualifiedlead", "date_entered_sql"), ("opportunity", "date_entered_opportunity"),
               ("customer", "date_entered_customer")]


def check_dates(c: Contact) -> None:
    stamped = [(s, parse_date(c.row.get(f, ""))) for s, f in DATE_FIELDS]
    stamped = [(s, d) for s, d in stamped if d]
    for i, (s1, d1) in enumerate(stamped):
        for s2, d2 in stamped[i + 1:]:
            if d2 < d1:
                c.findings.append(Finding(c.record_id, c.email, "DATE_INVERSION", "medium",
                                          f"Entered '{s1}' on {d1} but the later stage '{s2}' on {d2}. "
                                          "Stage-to-stage conversion and velocity reports will be wrong.",
                                          review_note="Usually caused by a backward move. Decide which date is true."))
                return


def check_duplicates(contacts: list[Contact], policy: dict) -> None:
    by_email = defaultdict(list)
    for c in contacts:
        if c.email:
            by_email[c.email].append(c)
    for email, group in by_email.items():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda x: rank(x.stage, policy))
        ids = ", ".join(x.record_id for x in group)
        for c in group:
            c.findings.append(Finding(c.record_id, c.email, "DUPLICATE_CONTACT", "medium",
                                      f"Same email on records {ids}. Duplicates split engagement and stage history.",
                                      review_note=f"Merge into record {keep.record_id} (furthest stage)."))


# ---------- history checks ----------

def audit_history(history: list[dict], contacts: dict[str, Contact], policy: dict) -> list[dict]:
    writers: dict[tuple, dict] = {}
    by_contact = defaultdict(list)
    for h in history:
        ts = parse_ts(h.get("timestamp", ""))
        if not ts:
            continue
        stype = SOURCE_TYPES.get(h.get("source_type", "").upper(), h.get("source_type", "OTHER").upper())
        h = dict(h, ts=ts, stype=stype)
        by_contact[h["record_id"]].append(h)
        key = (stype, h.get("source_name", ""))
        w = writers.setdefault(key, {"source_type": stype, "source_name": key[1], "writes": 0, "contacts": set(),
                                     "forward": 0, "backward": 0, "clears": 0, "same_value": 0,
                                     "non_native_written": 0, "overlaps_with": set()})
        w["writes"] += 1
        w["contacts"].add(h["record_id"])
        old, _ = normalise_stage(h.get("old_value", ""), policy)
        new, new_rule = normalise_stage(h.get("new_value", ""), policy)
        if not h.get("new_value"):
            w["clears"] += 1
        elif new_rule:
            w["non_native_written"] += 1
        elif old == new:
            w["same_value"] += 1
        elif rank(new, policy) < rank(old, policy):
            w["backward"] += 1
        else:
            w["forward"] += 1

    # Name who set the current value on contacts marked Customer with no won deal.
    for rid, events in by_contact.items():
        c = contacts.get(rid)
        if not c:
            continue
        setters = [e for e in sorted(events, key=lambda e: e["ts"]) if e.get("new_value")]
        for f in c.findings:
            if f.code == "CUSTOMER_WITHOUT_WON_DEAL" and setters:
                last = setters[-1]
                f.evidence += f" Set by {last['stype']} '{last['source_name']}' on {last['ts']:%Y-%m-%d}."

    window = timedelta(hours=policy["writer_overlap_hours"])
    for rid, events in by_contact.items():
        events.sort(key=lambda e: e["ts"])
        c = contacts.get(rid)
        email = c.email if c else ""
        # Backward move: a clear followed by an earlier stage, or a direct downgrade.
        last_real = None
        for e in events:
            new, rule = normalise_stage(e.get("new_value", ""), policy)
            if not e.get("new_value"):
                continue
            if last_real and new and not rule and rank(new, policy) < rank(last_real, policy):
                if c:
                    c.findings.append(Finding(rid, email, "BACKWARD_MOVE", "high",
                                              f"{e['stype']} '{e['source_name']}' moved the stage back from "
                                              f"'{last_real}' to '{new}' on {e['ts']:%Y-%m-%d}.",
                                              review_note="Add a stage guard to that workflow's enrolment "
                                                          "(only Subscriber, Lead or blank)."))
                writers[(e["stype"], e["source_name"])]["backward"] += 1
            if new and not rule:
                last_real = new
        # Writer conflict: two different workflows writing the same contact inside the window.
        wf = [e for e in events if e["stype"] == "WORKFLOW"]
        for i, a in enumerate(wf):
            for b in wf[i + 1:]:
                if a["source_name"] != b["source_name"] and b["ts"] - a["ts"] <= window:
                    writers[("WORKFLOW", a["source_name"])]["overlaps_with"].add(b["source_name"])
                    writers[("WORKFLOW", b["source_name"])]["overlaps_with"].add(a["source_name"])
                    if c:
                        c.findings.append(Finding(rid, email, "WRITER_CONFLICT", "medium",
                                                  f"Workflows '{a['source_name']}' and '{b['source_name']}' both wrote "
                                                  f"Lifecycle Stage within {int((b['ts'] - a['ts']).total_seconds() // 60)} minutes.",
                                                  review_note="Pick one workflow to own each stage change."))

    out = []
    for w in writers.values():
        flags = []
        if w["backward"]:
            flags.append("moves stages backward")
        if w["non_native_written"]:
            flags.append("writes non-native values")
        if w["overlaps_with"]:
            flags.append("overlaps with other workflows")
        if w["same_value"]:
            flags.append("redundant writes")
        out.append({"source_type": w["source_type"], "source_name": w["source_name"], "writes": w["writes"],
                    "contacts": len(w["contacts"]), "forward": w["forward"], "backward": w["backward"],
                    "clears": w["clears"], "same_value": w["same_value"],
                    "non_native_written": w["non_native_written"],
                    "overlaps_with": "; ".join(sorted(w["overlaps_with"])), "flags": "; ".join(flags)})
    out.sort(key=lambda x: (-(x["backward"] + x["non_native_written"] + bool(x["overlaps_with"])), x["source_name"]))
    return out


# ---------- decisions and outputs ----------

def decide(c: Contact) -> tuple[dict | None, bool]:
    """Pick one fix per contact. A contact with any review finding goes to review, not to the import."""
    needs_review = any(f.confidence == "review" and f.code not in ("DATE_INVERSION", "WRITER_CONFLICT")
                       for f in c.findings)
    safe = [f for f in c.findings if f.confidence == "safe"]
    if not safe or needs_review:
        return None, needs_review or bool(safe)
    # Furthest safe stage wins; keep any lead status fix.
    order = ["lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer"]
    best = max(safe, key=lambda f: order.index(f.fix_stage) if f.fix_stage in order else -1)
    lead_status = next((f.fix_lead_status for f in safe if f.fix_lead_status), "")
    return {"Record ID": c.record_id, "Email": c.row.get("email", "").strip(),
            "Lifecycle Stage": best.fix_stage, "Lead Status": lead_status,
            "Clear stage first": "yes" if any(f.clear_first for f in safe) else "no",
            "Reason": " | ".join(f.code for f in safe)}, False


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def llm_notes(summary: dict) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "You are a marketing ops lead writing a short audit note for a HubSpot portal. Use ONLY the facts "
            "in this JSON. Do not invent numbers. Return JSON with keys 'summary' (3 to 5 sentences, plain words, "
            "no emojis) and 'review_notes' (object of record_id to one sentence saying what a human should check).\n\n"
            + json.dumps(summary, default=str)
        )
        msg = client.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"), max_tokens=900,
                                     messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception as e:  # never let the optional step break the audit
        print(f"  Claude step skipped ({type(e).__name__}); using templates.")
        return None


def template_summary(counts: Counter, n: int, n_safe: int, n_review: int, writers: list[dict]) -> str:
    bad_writers = [w["source_name"] for w in writers if w["flags"] and w["source_type"] == "WORKFLOW"]
    parts = [f"I audited {n} contacts and found {sum(counts.values())} issues."]
    if counts:
        top = ", ".join(f"{k.replace('_', ' ').lower()} ({v})" for k, v in counts.most_common(3))
        parts.append(f"The most common were {top}.")
    parts.append(f"{n_safe} contacts have a safe fix ready to import and {n_review} need a human decision.")
    if bad_writers:
        parts.append("Fix these workflows before importing, or the same problems come back: "
                     + ", ".join(sorted(set(bad_writers))) + ".")
    return " ".join(parts)


def run(contacts_path: Path, history_path: Path | None, policy_path: Path, out_dir: Path, as_of: date) -> dict:
    policy = json.loads(policy_path.read_text())
    contacts = build_contacts(load_csv(contacts_path), policy)
    before = Counter(display(c.stage, c.raw_stage) for c in contacts)

    for c in contacts:
        check_value(c, policy)
        check_deal_evidence(c, policy)
        check_stuck(c, policy, as_of)
        check_dates(c)
    check_duplicates(contacts, policy)
    writers = audit_history(load_csv(history_path), {c.record_id: c for c in contacts}, policy) \
        if history_path and history_path.exists() else []

    out_dir.mkdir(parents=True, exist_ok=True)
    fixes, review, findings = [], [], []
    after = Counter()
    for c in contacts:
        fix, needs_review = decide(c)
        findings.extend(asdict(f) for f in c.findings)
        if fix:
            fixes.append(fix)
            after[display(fix["Lifecycle Stage"])] += 1
        elif c.stage and not c.custom_rule:
            after[display(c.stage)] += 1
        else:
            after["(needs review)"] += 1
        if needs_review:
            review.append({"record_id": c.record_id, "email": c.email, "current_stage": " ".join(c.raw_stage.split()),
                           "issues": " | ".join(sorted({f.code for f in c.findings})),
                           "what_to_check": " ".join(dict.fromkeys(f.review_note for f in c.findings if f.review_note))})

    counts = Counter(f["code"] for f in findings)
    summary = {"contacts": len(contacts), "issue_counts": dict(counts), "safe_fixes": len(fixes),
               "review": review, "writers": [w for w in writers if w["flags"]],
               "funnel_before": dict(before), "funnel_after_safe_fixes": dict(after)}
    llm = llm_notes(summary)
    if llm:
        for r in review:
            r["what_to_check"] = llm.get("review_notes", {}).get(r["record_id"], r["what_to_check"])

    write_csv(out_dir / "audit_findings.csv", findings, list(Finding.__dataclass_fields__))
    write_csv(out_dir / "hubspot_lifecycle_fix_import.csv", fixes,
              ["Record ID", "Email", "Lifecycle Stage", "Lead Status", "Clear stage first", "Reason"])
    write_csv(out_dir / "review_queue.csv", review,
              ["record_id", "email", "current_stage", "issues", "what_to_check"])
    if writers:
        write_csv(out_dir / "workflow_writers.csv", writers, list(writers[0].keys()))

    text = llm["summary"] if llm and llm.get("summary") else template_summary(counts, len(contacts), len(fixes), len(review), writers)
    lines = [f"# Lifecycle Stage audit, {as_of}", "", text, "", "## Funnel by stage", "",
             "| Stage | Before | After safe fixes |", "|---|---|---|"]
    order = [display(x) for x in policy["native_order"] + policy.get("unordered_native", [])]
    for s in sorted(set(before) | set(after), key=lambda x: (order.index(x) if x in order else len(order), x)):
        lines.append(f"| {s} | {before.get(s, 0)} | {after.get(s, 0)} |")
    lines += ["", "## Who writes Lifecycle Stage", ""]
    for w in writers:
        lines.append(f"- {w['source_type']} '{w['source_name']}': {w['writes']} write{'s' if w['writes'] != 1 else ''} "
                     f"on {w['contacts']} contact{'s' if w['contacts'] != 1 else ''}"
                     + (f". Flags: {w['flags']}" if w["flags"] else ""))
    lines += ["", "## Needs a human decision", ""]
    lines += [f"- {r['record_id']} ({r['email']}): {r['issues']}. {r['what_to_check']}" for r in review] or ["- None"]
    lines += ["", "## Before you import the fix file", "",
              "1. Fix or pause the flagged workflows first.",
              "2. Export a before-state snapshot of every contact in the fix file.",
              "3. For rows with 'Clear stage first = yes', clear Lifecycle Stage, then import the new value. "
              "HubSpot will not move a stage backward or off a custom value otherwise.",
              "4. Test on five records, then import the rest."]
    (out_dir / "AUDIT_BRIEF.md").write_text("\n".join(lines) + "\n")
    summary["mode"] = "Claude" if llm else "rules only (no API key)"
    summary["contacts_obj"] = contacts
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--contacts", default=str(HERE / "sample_contacts.csv"))
    p.add_argument("--history", default=str(HERE / "sample_stage_history.csv"))
    p.add_argument("--policy", default=str(HERE / "lifecycle_policy.json"))
    p.add_argument("--out", default=str(HERE / "out"))
    p.add_argument("--as-of", default="2026-10-30", help="Audit date, YYYY-MM-DD or 'now'.")
    a = p.parse_args(argv)
    as_of = date.today() if a.as_of == "now" else date.fromisoformat(a.as_of)
    if not Path(a.contacts).exists():
        print(f"Contacts file not found: {a.contacts}")
        return 1
    s = run(Path(a.contacts), Path(a.history) if a.history else None, Path(a.policy), Path(a.out), as_of)
    print(f"Loaded {s['contacts']} contacts. Mode: {s['mode']}")
    for c in s["contacts_obj"]:
        codes = sorted({f.code for f in c.findings})
        label = " ".join(c.raw_stage.split()) or "(blank)"
        print(f"  {c.record_id:<6} {c.email:<22} {label:<24} {', '.join(codes) if codes else 'ok'}")
    flagged = s["writers"]
    if flagged:
        print("\nWriters to fix first:")
        for w in flagged:
            print(f"  {w['source_type']:<11} {w['source_name']:<28} {w['flags']}")
    print(f"\nSafe fixes ready to import: {s['safe_fixes']}. In review queue: {len(s['review'])}.")
    print(f"Outputs written to {os.path.relpath(a.out)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
