# The pattern: audit a property that many systems write

Lifecycle Stage is one example of a common GTM Engineering problem: one field that drives reporting and routing, written by forms, imports, integrations, workflows and people. The same pattern works for Lead Status, Contact Owner, Lead Source and Salesforce Opportunity Stage.

## 1. Normalise before you judge

Exports show labels ("Marketing Qualified Lead"), the API shows internal names (`marketingqualifiedlead`), and people add spaces and capitals. Map all of these to one value first. If you skip this step, half your findings are formatting noise and nobody trusts the report.

Values that are not native at all (Disqualified, Trial User, a numeric ID) are a different problem. They usually mean someone used the stage field to hold a second idea. The fix is to move that idea to the property it belongs in, such as Lead Status or a product status field, rather than to delete the option and orphan the records.

## 2. Check the value against evidence, not against opinion

A stage is a claim. Deals, scores and owners are evidence. The checks here compare the two:

- An open deal means at least Opportunity. A closed-won deal means Customer.
- Customer with no won deal is suspicious, but the evidence might sit in billing, so it goes to a human.
- A score over the MQL threshold and still at Lead after 30 days means the workflow is not firing, which is a bigger finding than the record itself.
- An MQL with no owner is a routing failure. Changing the stage would hide it.

## 3. Audit the writers, not just the records

Fixing records without fixing the writers means doing it again next quarter. The history file answers: who wrote this field, did they move it backward, did they write values that are not native, and did two of them touch the same contact within a day? The writer report is often the most useful output for an ops lead, because it turns thousands of bad records into a short list of workflows to change.

## 4. Split safe from risky

Only forward moves backed by evidence go into the import file. Anything that moves a stage backward, changes a Customer, merges duplicates or needs context goes to the review queue with a reason. This is the rule that makes a bulk fix something an ops lead will actually approve.

## 5. Respect the platform's rules

HubSpot moves Lifecycle Stage forward only unless the value is cleared first. The fix file says which rows need the clear, so the import does not silently fail on those records.

## Where else to use it

- Lead Source overwritten by every import: normalise, check against first-touch data, rank the importers.
- Contact Owner churn: find workflows and integrations that reassign owners and fight round robin.
- Salesforce Opportunity Stage: find deals that skip stages or move backward after close.
