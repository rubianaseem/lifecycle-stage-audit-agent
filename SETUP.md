# Setup

## 1. Install

You need Python 3.10 or newer.

```bash
git clone https://github.com/rubianaseem/lifecycle-stage-audit-agent.git
cd lifecycle-stage-audit-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run on the sample data

```bash
python lifecycle_audit_agent.py
python -m unittest discover tests
```

The sample has eight contacts and thirteen history rows, each built to show one problem: a numeric custom value, a "Trial User" value, a lead with an open deal, a Customer set by an old import, a high-score lead nobody moved, an MQL with no owner, a duplicate email and a workflow that pushed a Customer back to MQL.

Everything lands in `out/`:

| File | What it is |
|---|---|
| `audit_findings.csv` | Every finding with severity, evidence and the proposed fix |
| `hubspot_lifecycle_fix_import.csv` | Only the safe, forward, evidence-backed fixes, ready to import |
| `review_queue.csv` | Contacts a human must decide on, with what to check |
| `workflow_writers.csv` | Every source that wrote Lifecycle Stage, with its flags |
| `AUDIT_BRIEF.md` | A one-page summary with the before and after funnel |

The audit date defaults to 2026-10-30 so the sample output stays the same. Use `--as-of now` on real data.

## 3. Add an Anthropic key (optional)

```bash
cp .env.example .env
# then set ANTHROPIC_API_KEY=sk-ant-...
```

With a key, Claude writes the brief summary and one review note per contact. It only sees the findings JSON, never your raw export. If the call fails, the agent falls back to its templates and carries on.

## 4. Use your own HubSpot data

**Contacts file.** In HubSpot, build a contacts view with these columns and export it as CSV, then rename the headers to match `sample_contacts.csv`:

- Record ID, Email, Lifecycle Stage, Lead Status, HubSpot Score (or your score property), Contact owner, Create date
- The date-entered stamps for Lead, MQL, SQL, Opportunity and Customer. Their names differ by portal age, so search your properties for "Date entered" or "Became a".
- `deal_stages`: the stage of each associated deal, separated by `;`. The easiest way is a deal export joined on contact ID in a spreadsheet.

**History file.** HubSpot shows property history on each record but has no bulk export button for it. Use the helper, which calls the CRM v3 contacts endpoint with `propertiesWithHistory=lifecyclestage`:

1. In HubSpot, create a private app with the `crm.objects.contacts.read` scope and copy its token.
2. Run `HUBSPOT_TOKEN=pat-... python export_history.py --out my_history.csv`.
3. To see the transform without a token: `python export_history.py --from-json docs/sample_api_response.json --out /tmp/history.csv`.

Workflow writes come back with source type `AUTOMATION_PLATFORM`, and the agent reads that as WORKFLOW. If a row shows an ID instead of a workflow name, look the ID up once and replace it, because the writer report is only useful with names.

**Run it:**

```bash
python lifecycle_audit_agent.py --contacts my_contacts.csv --history my_history.csv --as-of now
```

**Importing the fixes.** Fix or pause the flagged workflows first. For rows where "Clear stage first" is `yes`, clear Lifecycle Stage on those records (a one-off workflow or an import with a blank value), then import the new value. HubSpot only moves Lifecycle Stage forward unless the value is cleared first. Test on five records before the rest.

## 5. Extend it with an AI coding tool

Open the folder in Cursor, Claude Code, Codex CLI or Grok and ask for one change at a time. Prompts that work well:

- "Add a check that flags SQLs with no activity in 21 days. Put the threshold in lifecycle_policy.json and add a test."
- "Read the company Lifecycle Stage too and flag contacts that are ahead of their company."
- "Post the AUDIT_BRIEF.md summary to a Slack webhook from SLACK_WEBHOOK_URL, and skip it if the variable is missing."
- "Add a Salesforce mode that reads Lead Status and Opportunity stage instead of HubSpot Lifecycle Stage."

Ask the tool to run `python -m unittest discover tests` after each change.

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| Every contact shows NON_NATIVE_VALUE | Your export uses labels the agent does not know. Add them to `aliases` in `lifecycle_policy.json`. |
| A custom value shows as "Unknown value" | Add it to `custom_options` with an action: `move_to_lead_status` or `review`. |
| No writer report | The history file is missing or its headers differ. Compare with `sample_stage_history.csv`. |
| STUCK_AT_LEAD fires on everyone | Your score scale is different. Change `mql_score_threshold`. |
| Dates are ignored | Use YYYY-MM-DD or DD/MM/YYYY. US-style dates work only when the day is over 12, so convert them first. |
| `ModuleNotFoundError: anthropic` | Activate the venv and run `pip install -r requirements.txt`. The agent works without it only if no key is set. |
| HTTP 401 from `export_history.py` | The token is wrong or the private app lacks `crm.objects.contacts.read`. |
