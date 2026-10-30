# Lifecycle Stage Audit Agent

An agent that audits HubSpot Lifecycle Stage across your contacts and its property history, finds why the funnel report is wrong, and writes a safe fix file plus a review queue for everything a human should decide.

Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

## The pain point

Lifecycle Stage is the property every funnel report, MQL handoff and conversion rate depends on, and it quietly rots. Custom values get added to the native property, imports set stages by hand, and several workflows write the same field with slightly different triggers. Nobody notices until marketing and sales disagree on the numbers.

> "Also found custom values (Disqualified, Unqualified, Trial User) injected directly into the native Lifecycle Stage property, with numeric internal names instead of standard ones"
> [r/hubspot: "99% of leads stuck at Lead stage in HubSpot"](https://www.reddit.com/r/hubspot/comments/1vnr4c2/99_of_leads_stuck_at_lead_stage_in_hubspot/)

> "Three workflows all setting Lifecycle Stage to MQL based on slightly different triggers. Contacts bouncing between stages, re-enrollment firing unexpectedly, reports not adding up."
> [r/hubspot: "What HubSpot workflow mistake creates the messiest CRM later?"](https://www.reddit.com/r/hubspot/comments/1tuyfc7/what_hubspot_workflow_mistake_creates_the/)

## What it does

- Reads a contact export (stage, lead status, score, owner, deal stages, date-entered stamps) and the Lifecycle Stage property history (old value, new value, who or what wrote it).
- Normalises labels and internal names, then flags custom values inside the native property, including numeric internal names the funnel report treats as separate stages.
- Checks each stage against evidence: open deals still at Lead, Customers with no closed-won deal, high-score leads stuck because no workflow fires, MQLs with no owner, date stamps in the wrong order, and duplicate contacts.
- Reads the history to catch backward moves (clear, then set an earlier stage) and two workflows writing the same contact within 24 hours, and ranks every writer so you fix the cause, not just the records.
- Splits the results: forward fixes backed by evidence go into a HubSpot import file, with a "clear stage first" flag where HubSpot needs it. Anything risky goes to a review queue with what to check.
- Runs on rules with no API key. With an Anthropic key, Claude writes the brief summary and review notes from the findings only.

## Where each step runs

1. **GitHub** is where the code lives. You copy it to your laptop with `git clone`.
2. **HubSpot** is where you export the two input files: a contacts view and the Lifecycle Stage history. [SETUP.md](SETUP.md) shows how.
3. **Terminal** (Mac Terminal or the terminal inside Cursor) is where you run the script. It reads the CSVs and writes everything to `out/`.
4. **Claude is optional.** With no key it uses built-in rules and templates. With a key in `.env` it writes a sharper brief. It still runs in the terminal.
5. **Back in HubSpot**, you fix the flagged workflows first, then import `out/hubspot_lifecycle_fix_import.csv` after a test on five records. The agent never writes to your CRM itself.

## Quick start

```bash
git clone https://github.com/rubianaseem/lifecycle-stage-audit-agent.git
cd lifecycle-stage-audit-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python lifecycle_audit_agent.py
```

On your own data: `python lifecycle_audit_agent.py --contacts my_contacts.csv --history my_history.csv --as-of now`

## Example output

```
Loaded 8 contacts. Mode: rules only (no API key)
  101    priya@northwind.io     marketingqualifiedlead   DUPLICATE_CONTACT, MQL_NO_OWNER, WRITER_CONFLICT
  102    tom@brightpath.com     Lead                     STUCK_AT_LEAD
  103    ana@quill.co           1234567                  NON_NATIVE_VALUE
  104    jo@lumen.ai            Customer                 CUSTOMER_WITHOUT_WON_DEAL
  105    sam@acme.com           lead                     STAGE_BEHIND_DEAL
  106    li@acme.com            Trial User               NON_NATIVE_VALUE
  107    priya@northwind.io     Subscriber               DUPLICATE_CONTACT
  108    ben@orbit.dev          customer                 BACKWARD_MOVE, DATE_INVERSION

Writers to fix first:
  WORKFLOW    MQL - demo request           overlaps with other workflows
  WORKFLOW    MQL - score over 70 (v2)     moves stages backward
  WORKFLOW    MQL - webinar attendee       overlaps with other workflows; redundant writes
  INTEGRATION Product signup sync          writes non-native values
  USER        k.lee                        writes non-native values

Safe fixes ready to import: 3. In review queue: 5.
Outputs written to out/
```

Notice that record 104 is marked Customer with only a closed-lost deal, and the history shows a 2024 import set it. The agent does not downgrade it. A wrong Customer flag is a question for finance or CS, not a bulk import.

## More

- [SETUP.md](SETUP.md): install, API key, exporting contacts and property history from HubSpot, extending with Cursor, Claude Code, Codex CLI or Grok, and troubleshooting.
- [docs/DETAILS.md](docs/DETAILS.md): cost note, stack-swap table, how to customise the policy file, and how this runs today versus what production would add.
- [docs/LIFECYCLE_PATTERN.md](docs/LIFECYCLE_PATTERN.md): the transferable GTM Engineering pattern behind it (normalise, check against evidence, audit the writers, split safe from risky) and why each rule exists.
