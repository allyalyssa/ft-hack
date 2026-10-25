# Collections agent: open design questions

## Context

An AI company's accounts-receivable team has a group of collectors, many of them working offsite. Collectors chase
customers through Salesforce, Slack and email, and log updates in a Google Sheet, Notion or the CRM. There are tens of
thousands of open invoices. Collectors spend the same effort on a $40 invoice as on a $150,000 one, so small invoices
get lost. Some invoices are only a few cents and should never have been issued.

The team is building a collections agent that runs fully offline on an NVIDIA GB10 box. Data arrives on a schedule as
an export bundle (Salesforce invoices, the collector sheet, Slack threads). A deterministic triage step picks the small
invoices that have been forgotten. A local model then drafts customer emails, Slack messages, Salesforce notes and
internal emails to account owners. A second model checks every proposed write-off. A person approves everything before
it is sent, because the box has no internet.

Triage policy:
- Queue an invoice if it is not paid, is at least 30 days past due, has not been touched in at least 8 days (or never),
  and has a remaining balance of $500 or less.
- Write off if the remaining balance is $5 or less.
- Escalate to a person if the invoice is disputed.
- Otherwise the agent handles it.

Demo world (fake data, 20,000 invoices, 1,198 accounts, 12 account owners):
- 13,004 open invoices; 9,200 of them are $500 or less.
- 5,332 queued, worth $510,042. Agent 4,180 ($428,906), write-off 391 ($900), escalate 761 ($80,236).
- 1,804 queued invoices were never touched by anyone.
- 752 open invoices have touch dates that disagree between sources by 8 or more days (for example the collector sheet
  says touched this week, Salesforce says three weeks ago); 134 of those are in the queue.
- At 6 minutes per touch, the queue is about 533 collector hours.
- A live "delta" drop two days later grows the queue from 5,332 to 5,688: 12 invoices in the drop newly cross 30 days,
  10 queued invoices were paid and leave, and the rest of the growth is the whole world aging two days.

People with a stake: offsite collectors, the collections manager, the finance controller who owns write-off policy,
account owners on the go-to-market team, the CFO, and hackathon judges from NVIDIA and AI companies.

## Questions

### Q1. Disputed invoices under $5: write off or escalate?
- Option A: write off. A disputed $2 invoice costs more to argue about than it is worth.
- Option B: escalate. A dispute is a customer-relationship signal and should reach a person regardless of amount.

### Q2. How should the live delta drop be explained in the demo?
- Option A: show only the total change (5,332 to 5,688) and say "two days later, 356 more invoices slipped through".
- Option B: show a diff since the last run: newly queued, left the queue (paid or touched), and still queued.

### Q3. Which source-conflict number goes on the slide?
- Option A: 752, all open invoices where Salesforce and the collector sheet disagree. Shows how bad the data is.
- Option B: 134, only the conflicts inside the agent's queue. Shows what the agent itself has to handle.

### Q4. How should time saved be stated?
- Option A: one-time backlog, about 533 collector hours to clear the current queue.
- Option B: a recurring monthly figure, which needs an assumption about how many invoices go stale each month.

### Q5. Should the $500 limit apply to the remaining balance or the original invoice amount?
- Option A: remaining balance. A $900 invoice with $600 paid is a $300 problem.
- Option B: original amount. Partially paid large invoices stay with human collectors because the customer is engaged.
