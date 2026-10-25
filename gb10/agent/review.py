#!/usr/bin/env python3
"""Review collections packages from the shell. The OpenClaw skill shells out to this.

    python3 agent/review.py list [--state drafted] [--bucket agent] [--limit 20]
    python3 agent/review.py show INV-0042
    python3 agent/review.py approve INV-0042 --by "Mohammed"
    python3 agent/review.py reject INV-0042 --by "Mohammed"
    python3 agent/review.py edit INV-0042 customer_slack.md --text "..." --by "Mohammed"

Approve, reject and edit update out/actions/<id>/status.json and the latest case row in
agent/crm_mock/crm.sqlite, the same writes the dashboard makes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import actions as A  # noqa: E402  (same folder, role C)

LABELS = {
    "customer_email.md": "Customer email",
    "customer_slack.md": "Customer Slack message",
    "internal_note.md": "Internal note",
    "salesforce_case.json": "Salesforce case",
    "writeoff_memo.md": "Write-off memo",
}
STATE_FOR = {"approve": "approved", "reject": "rejected"}
BUCKET_FOR_ACTION = {action: bucket for bucket, action in A.BUCKET_ACTION.items()}


def package_dir(invoice_id):
    if not A.INVOICE_ID_RE.match(invoice_id or ""):
        raise SystemExit(f"not an invoice id: {invoice_id!r}")
    return A.ACTIONS_DIR / invoice_id


def crm_update(invoice_id, **fields):
    """Update the latest case row for the invoice. Returns its case_id, or None."""
    if not A.CRM_PATH.exists():
        return None
    try:
        with closing(sqlite3.connect(str(A.CRM_PATH), timeout=10)) as con:
            with con:
                row = con.execute("SELECT MAX(case_id) FROM cases WHERE invoice_id = ?", (invoice_id,)).fetchone()
                if not row or row[0] is None:
                    return None
                assignments = ", ".join(f"{column} = ?" for column in fields)
                con.execute(f"UPDATE cases SET {assignments} WHERE case_id = ?", (*fields.values(), row[0]))
                return row[0]
    except sqlite3.Error as exc:
        print(f"CRM update failed for {invoice_id}: {exc}", file=sys.stderr)
        return None


def crm_rows(invoice_ids=None):
    if not A.CRM_PATH.exists():
        return {}
    try:
        with closing(sqlite3.connect(str(A.CRM_PATH), timeout=10)) as con:
            rows = con.execute("SELECT invoice_id, account_name, amount FROM invoices").fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: {"account_name": r[1], "amount": r[2]} for r in rows if not invoice_ids or r[0] in invoice_ids}


def set_state(invoice_id, state, by):
    path = package_dir(invoice_id) / "status.json"
    status = A.read_json(path)
    if not isinstance(status, dict):
        raise SystemExit(f"{invoice_id}: no package yet. Draft one with: python3 agent/actions.py --invoice {invoice_id}")
    status.update(state=state, by=by, at=A.iso_now())  # keeps action_id
    A.write_json(path, status)
    return status, crm_update(invoice_id, state=state)


def bucket_of(status, case):
    parts = str(status.get("action_id") or "").split(":")
    if len(parts) >= 3 and parts[1] in BUCKET_FOR_ACTION:
        return BUCKET_FOR_ACTION[parts[1]]
    return "writeoff" if case.get("case_type") == "writeoff" else ""


def cmd_list(args):
    rows = []
    if A.ACTIONS_DIR.is_dir():
        for folder in sorted(A.ACTIONS_DIR.iterdir()):
            status = A.read_json(folder / "status.json") if folder.is_dir() else None
            if not isinstance(status, dict):
                continue
            case = A.read_json(folder / "salesforce_case.json") or {}
            bucket = bucket_of(status, case)
            if args.state and status.get("state") != args.state:
                continue
            if args.bucket and bucket != args.bucket:
                continue
            rows.append((folder.name, status, case, bucket))
    crm = crm_rows({r[0] for r in rows})
    rows.sort(key=lambda r: (r[1].get("state") not in ("drafted", "edited"), -(crm.get(r[0], {}).get("amount") or 0)))
    if not rows:
        print("No packages match.")
        return 0
    for invoice_id, status, case, bucket in rows[: args.limit]:
        info = crm.get(invoice_id, {})
        amount = A.money(info["amount"]) if info.get("amount") is not None else ""
        print(f"{invoice_id:<14} {status.get('state', ''):<9} {bucket or '-':<9} {amount:>11}  "
              f"{(info.get('account_name') or '')[:34]:<34}  {case.get('model', '')}")
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more (use --limit)")
    return 0


def cmd_show(args):
    folder = package_dir(args.invoice_id)
    status = A.read_json(folder / "status.json")
    if not isinstance(status, dict):
        print(f"{args.invoice_id}: no package yet. Draft one with: python3 agent/actions.py --invoice {args.invoice_id}")
        return 1
    case = A.read_json(folder / "salesforce_case.json") or {}
    info = crm_rows({args.invoice_id}).get(args.invoice_id, {})
    amount = A.money(info["amount"]) if info.get("amount") is not None else ""
    print(f"{args.invoice_id}  {info.get('account_name', '')}  {amount}".rstrip())
    who = f" by {status['by']}" if status.get("by") else ""
    print(f"State: {status.get('state')}{who} at {status.get('at')}")
    print(f"Action: {status.get('action_id', '')}   Model: {case.get('model', '')}")
    for name, label in LABELS.items():
        path = folder / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").rstrip()
        if name == "salesforce_case.json":
            text = "\n".join([f"Type: {case.get('case_type', '')}", f"Summary: {case.get('summary', '')}",
                              f"Next action: {case.get('next_action', '')} (by {case.get('next_action_date', '')})"])
        elif name == "customer_slack.md":
            label += f", {len(text)} characters"
        print(f"\n--- {label}\n{text}")
    return 0


def cmd_set(args):
    state = STATE_FOR[args.command]
    status, case_id = set_state(args.invoice_id, state, args.by)
    crm = f" CRM case #{case_id} set to {state}." if case_id else " No CRM case row found."
    print(f"{args.invoice_id} {state} by {status['by']} at {status['at']}.{crm}")
    return 0


def cmd_edit(args):
    folder = package_dir(args.invoice_id)
    if args.file not in LABELS:
        raise SystemExit(f"file must be one of: {', '.join(LABELS)}")
    if not (folder / args.file).exists():
        raise SystemExit(f"{args.invoice_id} has no {args.file}")
    if args.text is None and args.from_file is None:
        raise SystemExit("give --text or --from-file")
    content = args.text if args.text is not None else Path(args.from_file).read_text(encoding="utf-8")
    content = content.replace("\r\n", "\n").strip() + "\n"
    if args.file == "salesforce_case.json":
        try:
            case = json.loads(content)
        except ValueError as exc:
            raise SystemExit(f"salesforce_case.json is not valid JSON: {exc}")
        if not isinstance(case, dict):
            raise SystemExit("salesforce_case.json must be a JSON object")
        old = A.read_json(folder / "salesforce_case.json") or {}
        case["invoice_id"], case["action_id"] = old.get("invoice_id", args.invoice_id), old.get("action_id", "")
        content = json.dumps(case, indent=2, ensure_ascii=False) + "\n"
        crm_update(args.invoice_id, summary=str(case.get("summary", "")), next_action=str(case.get("next_action", "")),
                   next_action_date=str(case.get("next_action_date", "")))
    A.atomic_write(folder / args.file, content)
    status, case_id = set_state(args.invoice_id, "edited", args.by)
    print(f"{args.invoice_id} {args.file} saved, state edited by {status['by']} at {status['at']}.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="List, show, approve, reject or edit collections packages.")
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="packages, awaiting review first")
    ls.add_argument("--state", choices=["drafted", "edited", "approved", "rejected"])
    ls.add_argument("--bucket", choices=list(A.BUCKET_ORDER))
    ls.add_argument("--limit", type=int, default=20)
    show = sub.add_parser("show", help="print one package")
    show.add_argument("invoice_id")
    for name in ("approve", "reject"):
        cmd = sub.add_parser(name, help=f"{name} one package")
        cmd.add_argument("invoice_id")
        cmd.add_argument("--by", default="chat")
    edit = sub.add_parser("edit", help="replace one artifact and mark the package edited")
    edit.add_argument("invoice_id")
    edit.add_argument("file", help="customer_email.md, customer_slack.md, internal_note.md, writeoff_memo.md or salesforce_case.json")
    edit.add_argument("--text")
    edit.add_argument("--from-file")
    edit.add_argument("--by", default="chat")
    args = p.parse_args(argv)
    handlers = {"list": cmd_list, "show": cmd_show, "approve": cmd_set, "reject": cmd_set, "edit": cmd_edit}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
