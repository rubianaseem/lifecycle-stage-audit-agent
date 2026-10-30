# Details

## Cost note

With no API key the agent costs nothing to run. With a key it makes one Claude call per run, sending only the findings summary, not the full export, so the cost depends on how many contacts end up in the review queue rather than how many you audit. Check current pricing on Anthropic's site before running it on a large portal. The HubSpot calls in `export_history.py` count toward your private app's API limits; the helper backs off on HTTP 429.

## Stack-swap table

| Piece | In this repo | Swap for |
|---|---|---|
| CRM export | HubSpot contacts CSV | Salesforce Lead and Contact report, Dynamics export |
| Stage field | Lifecycle Stage | Salesforce Lead Status plus a custom funnel stage, a lifecycle field in your warehouse |
| History | HubSpot property history API | Salesforce Field History Tracking, a snapshot table in BigQuery or Snowflake |
| Rules | `lifecycle_policy.json` | A dbt model or a sheet ops owns |
| LLM | Claude via Anthropic | Any model with a JSON response |
| Fix delivery | CSV import | A HubSpot workflow, an n8n flow calling the batch update API |

## Customise for your stack

Everything opinionated lives in `lifecycle_policy.json`:

- `native_order`: your stages in funnel order. Drop Evangelist if you do not use it.
- `aliases`: extra labels your team or an integration uses for native stages.
- `custom_options`: every non-native value found in your property, and what to do with it. `move_to_lead_status` moves the meaning into Lead Status and resets the stage. `review` sends it to a human.
- `mql_score_threshold`, `stuck_lead_days`, `mql_owner_sla_days`, `writer_overlap_hours`: the thresholds behind each check.
- `closed_won_stages`, `closed_lost_stages`: your deal stage internal names, including every pipeline.

To add a check, write a function that takes a contact and appends a `Finding`, then call it in `run()`. Mark it `safe` only when the fix moves forward and evidence in the data supports it.

## How this runs today (and what production would add)

Today it is a script you run by hand on two CSV exports. It reads, decides and writes files. It never writes to HubSpot. You review the output and import it yourself.

Production would add:

- A scheduled run (weekly is enough) on a fresh export, with the brief posted to the ops channel.
- A snapshot table of Lifecycle Stage per contact per day, so backward moves are caught within a day, not at the next audit.
- A writer registry: one workflow per stage change, documented, with the agent alerting when a new source starts writing the property.
- A guarded write-back through the batch update API for the safe fixes only, with a before-state backup and a dry-run diff someone approves.
- The same audit for the company object, plus a check that contacts and their company agree.
