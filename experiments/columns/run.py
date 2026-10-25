"""Which queue columns change what a collector does? Simulated collectors on the local model, five dashboard views.

python experiments/columns/run.py                  # full run, writes experiments/columns/results/
python experiments/columns/run.py --per-class 2 --reps 1 --personas 1    # quick check

Each call shows one persona a batch of real queued invoices rendered as one dashboard view and asks for one next
action per invoice from a fixed menu. The expected action comes from the full record (notes, Slack, the account's
other queued invoices), so views that surface notes partly contain the answer: read the results as "how often does
the current view lead to the wrong touch", not as proof that a column is useful to people. Synthetic agents are not
human evidence.
"""
import argparse
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
import actions  # noqa: E402
import store  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
ACTIONS = {
    "send_reminder": "send the standard payment reminder for this invoice",
    "bundle_account": "send one reminder covering all of this customer's open invoices together",
    "ask_remittance": "ask the customer for remittance details because they say it is already paid",
    "wait_for_promise": "do not contact yet; the customer recently promised a payment date",
    "fix_po": "get the PO number or vendor-portal upload sorted so the invoice can be paid",
    "resend_invoice": "resend or consolidate the invoice paperwork the customer asked for",
    "find_contact": "find a new billing contact because the current one is gone",
    "route_to_owner": "hand it to the account owner or deal desk because the customer questions the charge",
}
BY_BLOCKER = {
    "already_paid": "ask_remittance",
    "payment_promised": "wait_for_promise",
    "missing_po": "fix_po",
    "invoice_admin": "resend_invoice",
    "bad_contact": "find_contact",
    "usage_overage": "route_to_owner",
}
HARMFUL = {"ask_remittance", "wait_for_promise"}
GENERIC = {"send_reminder", "bundle_account"}
VIEWS = ("current", "exposure", "note", "blocker", "all_three")
PERSONAS = (
    "a collector in your first week, following the team playbook to the letter",
    "a veteran collector with ten years in accounts receivable",
    "an overloaded collector covering 400 accounts who has to move fast",
    "a relationship-minded collector who avoids annoying good customers",
)
PROMISE_FRESH_DAYS = 30


def expected(item, exposure, as_of):
    key = store.blocker(item)
    if key == "payment_promised":
        note_date = date.fromisoformat(store.latest_note(item)[0])
        if (as_of - note_date).days > PROMISE_FRESH_DAYS:
            key = None
    if key:
        return BY_BLOCKER[key]
    return "bundle_account" if exposure[item["account_id"]][0] >= 3 else "send_reminder"


def row(item, view, exposure):
    touch = (f"last touch {store.long_date(item['last_touch_date'])} via {item['last_touch_source']}"
             if item.get("last_touch_date") else "never touched")
    balance = store.money(item["balance"])
    if item["amount_paid"]:
        balance += f" of {store.money(item['amount'])}"
    cells = [item["invoice_id"], item["account_name"], f"owner {item['account_owner']}", f"balance {balance}",
             f"{item['days_past_due']} days past due", touch, f"status {item['status'].replace('_', ' ')}"]
    if view in ("exposure", "all_three"):
        count, dollars = exposure[item["account_id"]]
        cells.append(f"account has {count - 1} other queued invoices ({store.money(dollars - item['balance'])})"
                     if count > 1 else "no other queued invoices for this account")
    if view in ("note", "all_three"):
        note = store.latest_note(item)
        cells.append(f"latest note {store.long_date(note[0])}, {note[1].split(',')[0]}: \"{note[2]}\"" if note else "no notes")
    if view in ("blocker", "all_three"):
        key = store.blocker(item)
        cells.append(f"blocker: {store.BLOCKER_LABEL[key]}" if key else "blocker: none")
    return " | ".join(cells)


def ask(persona, rows, base_url, model):
    menu = "\n".join(f"- {k}: {v}" for k, v in ACTIONS.items())
    system = (f"You are {persona}. You are working your collections queue. For each invoice row, choose the single "
              f"next action from this menu:\n{menu}\n\nUse only what the row shows. Return one JSON object mapping each "
              f"invoice id to one action key.")
    user = "Today is October 25, 2026. Your queue:\n" + "\n".join(rows)
    body = {"model": model, "temperature": 0.7, "stream": False, "response_format": {"type": "json_object"},
            "reasoning_effort": "none", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    ids = [r.split(" | ")[0] for r in rows]
    for attempt in range(3):
        try:
            response = actions.post_json(base_url.rstrip("/") + "/chat/completions", body)
            return actions.parse(response["choices"][0]["message"]["content"], ids)
        except OSError as e:
            print(f"model call failed ({e}), attempt {attempt + 1} of 3", flush=True)
            time.sleep(10)
    return {i: "" for i in ids}


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


def sample(items, exposure, as_of, per_class, rng):
    by_truth = defaultdict(list)
    for item in items:
        by_truth[expected(item, exposure, as_of)].append(item)
    picked = []
    for truth in ACTIONS:
        pool = sorted(by_truth.get(truth, []), key=lambda i: i["invoice_id"])
        picked += rng.sample(pool, min(per_class, len(pool)))
    return picked


def score(decisions, key):
    groups = defaultdict(list)
    for d in decisions:
        groups[d[key]].append(d)
    lines = [f"| {key} | n | correct (95% CI) | harmful touch (95% CI) | bundled when it should (95% CI) |", "|---|---|---|---|---|"]
    for name in sorted(groups, key=lambda g: VIEWS.index(g) if g in VIEWS else g):
        rows = groups[name]
        correct = wilson(sum(d["choice"] == d["expected"] for d in rows), len(rows))
        harm_rows = [d for d in rows if d["expected"] in HARMFUL]
        harm = wilson(sum(d["choice"] in GENERIC for d in harm_rows), len(harm_rows))
        bundle_rows = [d for d in rows if d["expected"] == "bundle_account"]
        bundle = wilson(sum(d["choice"] == "bundle_account" for d in bundle_rows), len(bundle_rows))
        fmt = lambda t: f"{t[0]:.0%} ({t[1]:.0%}-{t[2]:.0%})"
        lines.append(f"| {name} | {len(rows)} | {fmt(correct)} | {fmt(harm)} | {fmt(bundle)} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=5, help="invoices sampled per expected action")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--personas", type=int, default=len(PERSONAS))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--from-raw", action="store_true", help="rescore results/raw.jsonl without calling the model")
    args = ap.parse_args()
    if args.from_raw:
        decisions = [json.loads(line) for line in (RESULTS / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
        return write_report(decisions, store.model_config()[1], "rescored from raw.jsonl")

    base_url, model = store.model_config()
    queue = store.load_queue()
    as_of = date.fromisoformat(queue["as_of"])
    exposure = store.account_exposure(queue["items"])
    rng = random.Random(args.seed)
    invoices = sample([i for i in queue["items"] if i["bucket"] == "agent"], exposure, as_of, args.per_class, rng)
    truth = {i["invoice_id"]: expected(i, exposure, as_of) for i in invoices}
    print(f"{len(invoices)} invoices: {dict(Counter(truth.values()))}; model {model}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    raw = (RESULTS / "raw.jsonl").open("w", encoding="utf-8")
    decisions, calls, started = [], 0, time.perf_counter()
    for rep in range(args.reps):
        for persona in PERSONAS[:args.personas]:
            for view in VIEWS:
                order = invoices[:]
                random.Random(f"{args.seed}:{rep}:{persona}:{view}").shuffle(order)
                for start in range(0, len(order), args.batch):
                    batch = order[start:start + args.batch]
                    rows = [row(i, view, exposure) for i in batch]
                    choices = ask(persona, rows, base_url, model)
                    calls += 1
                    for item in batch:
                        choice = choices.get(item["invoice_id"], "")
                        d = {"rep": rep, "persona": persona.split(",")[0], "view": view, "invoice_id": item["invoice_id"],
                             "expected": truth[item["invoice_id"]], "choice": choice if choice in ACTIONS else f"invalid:{choice}"}
                        decisions.append(d)
                        raw.write(json.dumps(d) + "\n")
                print(f"rep {rep} {persona[:30]:<30} done, {calls} calls, {time.perf_counter() - started:.0f}s", flush=True)
    raw.close()

    write_report(decisions, model, f"{calls} model calls in {time.perf_counter() - started:.0f}s")


def write_report(decisions, model, run_note):
    invalid = sum(d["choice"].startswith("invalid") for d in decisions)
    invoices = len({d["invoice_id"] for d in decisions})
    personas = len({d["persona"] for d in decisions})
    reps = len({d["rep"] for d in decisions})
    confusion = Counter((d["expected"], d["choice"]) for d in decisions if d["view"] == "current")
    report = f"""# Column ablation, simulated collectors

Model {model}; {invoices} queued agent-bucket invoices sampled evenly across expected actions; {personas} personas x
{len(VIEWS)} views x {reps} reps; {len(decisions)} decisions, {invalid} invalid or missing answers (scored wrong);
{run_note}.

Expected action: the newest note matching a blocker phrase decides (a promise counts only if under {PROMISE_FRESH_DAYS}
days old); otherwise bundle when the account has 3+ queued invoices, else a standard reminder. Harmful touch: a
standard or bundled reminder sent to a customer who says they paid or recently promised a date. Views that show notes
contain part of the answer, so their gains are partly by construction. Synthetic agents are not human evidence.

## By view

{score(decisions, "view")}

## By persona (all views)

{score(decisions, "persona")}

## Current view: expected -> chosen (counts)

| expected | chosen | n |
|---|---|---|
""" + "\n".join(f"| {e} | {c} | {n} |" for (e, c), n in sorted(confusion.items(), key=lambda x: (x[0][0], -x[1]))) + "\n"
    (RESULTS / "summary.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
