"""Read and decide on drafted packages from the command line. The OpenClaw skill calls this.

python agent/status.py list [--state drafted]
python agent/status.py show INV-116288
python agent/status.py approve INV-116288 --by "Dana"
python agent/status.py reject INV-116288 --by "Dana"
"""
import argparse

import store


def show(invoice_id):
    action = store.load_action(invoice_id)
    if action is None:
        raise SystemExit(f"{invoice_id} has no drafts. Draft it with: python3 agent/actions.py --invoice {invoice_id}")
    item, status, case = action["item"], action["status"], action["case"]
    print(f"{invoice_id}  {item['account_name']}  balance {store.money(item['balance'], item.get('currency', 'USD'))}  "
          f"{item['days_past_due']} days past due  bucket {item['bucket']}")
    print(f"state {status['state']} by {status['by']} at {status['at']}  case {case.get('case_number')}  model {case.get('model')}")
    for name, text in action["files"].items():
        print(f"\n--- {name}\n{text.strip()}")
    print(f"\n--- salesforce case\n{case.get('summary')}\nNext: {case.get('next_action')} ({case.get('next_action_date')})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--state", default="drafted", choices=("drafted", "edited", "approved", "rejected", "all"))
    sub.add_parser("show").add_argument("invoice_id")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("invoice_id")
        p.add_argument("--by", required=True)
    args = ap.parse_args()

    if args.command == "list":
        rows = [(s["at"], iid, s) for iid, s in store.all_statuses().items() if args.state in ("all", s["state"])]
        for at, iid, s in sorted(rows, reverse=True):
            item = store.read_json(store.action_dir(iid) / "queue_item.json", {})
            print(f"{iid}  {item.get('bucket', ''):<8}  {store.money(item.get('balance', 0)):>9}  "
                  f"{item.get('account_name', '')}  {s['state']} {at}")
        print(f"{len(rows)} {args.state}")
    elif args.command == "show":
        show(args.invoice_id)
    else:
        state = "approved" if args.command == "approve" else "rejected"
        try:
            status = store.set_state(args.invoice_id, state, args.by)
        except LookupError as e:
            raise SystemExit(str(e))
        store.build_summary(store.read_json(store.QUEUE))
        print(f"{args.invoice_id} {status['state']} by {status['by']} at {status['at']}")


if __name__ == "__main__":
    main()
