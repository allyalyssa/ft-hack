"""Write upload-ready invoice batches for the demo: new invoices on the existing accounts, same snapshot date.

python data/demo_batch.py                     # data/demo/batch-1 .. batch-3, 300 invoices each
python data/demo_batch.py --batches 5 --size 500

Each folder is one export bundle (salesforce_invoices.csv, collector_sheet.csv, slack_threads.json, manifest.json
with "kind": "delta"). Upload a folder's files on the dashboard's Upload page, or copy them into data/inbox/.
Invoice ids start at INV-130000 so they never collide with data/exports or the --delta drop.
"""
import argparse
import random
from datetime import date
from pathlib import Path

import generate as g

OUT = g.ROOT / "data" / "demo"


def build_batch(number, size, accounts, as_of, seed):
    rng = random.Random(seed)
    weights = [rng.paretovariate(1.3) for _ in accounts]
    picks = rng.choices(accounts, weights=weights, k=size)
    first = 130000 + (number - 1) * 1000
    invoices = [g.make_invoice(rng, f"INV-{first + i}", account, as_of) for i, account in enumerate(picks)]
    notes = g.make_notes(rng, invoices, as_of)
    open_invoices = [i for i in invoices if i["status"] != "paid"]
    slack = [g.make_thread(rng, inv, as_of) for inv in rng.sample(open_invoices, min(len(open_invoices), size // 12))]
    return invoices, notes, slack


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=3)
    ap.add_argument("--size", type=int, default=300)
    ap.add_argument("--as-of", type=date.fromisoformat, default=g.AS_OF)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    accounts = g.make_accounts(random.Random(g.SEED))
    for number in range(1, args.batches + 1):
        seed = g.SEED * 100 + number
        invoices, notes, slack = build_batch(number, args.size, accounts, args.as_of, seed)
        folder = args.out / f"batch-{number}"
        g.write_bundle(folder, invoices, notes, slack, {"kind": "delta", "as_of": args.as_of.isoformat(), "seed": seed,
                                                       "batch": number})
        print(f"batch-{number}: {len(invoices)} invoices, {len(notes)} sheet notes, {len(slack)} Slack threads -> {folder}")


if __name__ == "__main__":
    main()
