#!/usr/bin/env python3
"""Collections triage: which small invoices need a touch today. No model.

    python3 triage/triage.py                reads data/inbox/, or data/exports/ when the inbox is empty
    python3 triage/triage.py --input DIR    reads DIR instead

Joins the Salesforce export, the collector sheet and the Slack export on
invoice_id, counts the agent's approved outreach in out/actions/ as a touch,
applies policy.yaml, and writes out/queue.json (role C reads it) and
out/headline.json (the deck numbers). Standard library only.
"""
import time

_STARTED = time.perf_counter()

import argparse  # noqa: E402  (imports count toward the elapsed time)
import csv
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "inbox"
EXPORTS = ROOT / "data" / "exports"
OUT = ROOT / "out"

SALESFORCE = "salesforce_invoices.csv"
SHEET = "collector_sheet.csv"
SLACK = "slack_threads.json"
POLICY = "policy.yaml"
MANIFEST = "manifest.json"
REQUIRED = ("invoice_id", "amount", "due_date", "status", "last_touch_date")

BUCKETS = ("writeoff", "agent", "escalate")
MINUTES_PER_TOUCH = 6
POLICY_DEFAULTS = {
    "min_days_past_due": "30",
    "stale_touch_days": "8",
    "write_off_max_amount": "25.00",
    "agent_max_amount": "500.00",
    "currency": "USD",
}
NO_TOUCH = {"date": None, "channel": None, "source": None}
AGENT_TOUCH_STATES = ("approved", "edited")  # drafted and rejected packages never reached the customer
TOUCH_RANK = {"salesforce": 3, "agent": 2, "sheet": 1, "slack": 0}  # tie on the same day

_EPOCH = re.compile(r"^\d{9,}(\.\d+)?$")
# A collector note's channel is the first channel word in it ("Called...", "Emailed...").
_CHANNEL = re.compile(
    r"\b(?:(?P<phone>call(?:ed|s)?|spoke|voicemail|phoned?)"
    r"|(?P<email>e-?mail(?:ed|s)?|sent|resent)"
    r"|(?P<portal>portal))\b",
    re.IGNORECASE,
)


def warn(message):
    print(f"triage: {message}", file=sys.stderr)


def to_cents(text):
    """'1,234.50' or '$0.37' to integer cents. Integer math keeps the thresholds exact."""
    cleaned = str(text if text is not None else "").strip().replace(",", "").replace("$", "")
    return round(float(cleaned) * 100)


def to_date(text):
    value = str(text or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def to_datetime(text):
    """ISO timestamp or Slack epoch seconds, as a naive datetime. An ISO zone offset is
    dropped and the wall-clock time kept, so the date is the business date as written."""
    value = str(text or "").strip()
    if not value:
        return None
    if _EPOCH.match(value):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        day = to_date(value)
        return datetime(day.year, day.month, day.day) if day else None
    return moment.replace(tzinfo=None)


def to_bool(text):
    return str(text or "").strip().lower() in ("true", "1", "yes", "y", "t")


def note_channel(text):
    match = _CHANNEL.search(text or "")
    return match.lastgroup if match else "note"


def plain_number(value):
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def parse_policy(text, today=None):
    """policy.yaml is flat `key: value` lines, so no YAML library is needed."""
    raw = dict(POLICY_DEFAULTS)
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key not in POLICY_DEFAULTS and key != "as_of":
            warn(f"policy.yaml: unknown key {key!r} ignored")
        raw[key] = value.strip("'\"")
    as_of = to_date(raw.get("as_of"))
    if as_of is None:
        as_of = today or date.today()
        warn(f"policy.yaml has no as_of, using {as_of.isoformat()}")
    return {
        "as_of": as_of,
        "min_days_past_due": int(float(raw["min_days_past_due"])),
        "stale_touch_days": int(float(raw["stale_touch_days"])),
        "write_off_max_amount": float(raw["write_off_max_amount"]),
        "agent_max_amount": float(raw["agent_max_amount"]),
        "currency": raw["currency"],
    }


def load_policy(path):
    if path.is_file():
        return parse_policy(path.read_text(encoding="utf-8-sig"))
    warn(f"no {POLICY} in {path.parent}, using the CONTRACT.md defaults")
    return parse_policy("")


def load_manifest(path):
    """(snapshot_id, export date). ("local", None) when there is no manifest."""
    if not path.is_file():
        return "local", None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        warn(f"{path.name} is not valid JSON, ignoring it")
        return "local", None
    snapshot_id = str(manifest.get("snapshot_id") or "local")
    exported = to_datetime(manifest.get("exported_at"))
    if manifest.get("exported_at") and exported is None:
        warn(f"{path.name}: cannot read exported_at {manifest.get('exported_at')!r}, using policy as_of")
    return snapshot_id, exported.date() if exported else None


def index_notes(rows):
    """invoice_id -> [(date, note)], oldest first."""
    by_invoice = {}
    for row in rows:
        invoice_id = str(row.get("invoice_id") or "").strip()
        if not invoice_id:
            continue
        when = to_date(row.get("note_date"))
        promised = to_date(row.get("promise_to_pay_date"))
        by_invoice.setdefault(invoice_id, []).append((when, {
            "collector": str(row.get("collector") or "").strip(),
            "note": str(row.get("note") or "").strip(),
            "note_date": when.isoformat() if when else str(row.get("note_date") or "").strip(),
            "promise_to_pay_date": promised.isoformat() if promised else None,
            "dispute_flag": to_bool(row.get("dispute_flag")),
        }))
    for notes in by_invoice.values():
        notes.sort(key=lambda pair: pair[0] or date.min)
    return by_invoice


def index_slack(threads):
    """invoice_id -> [(datetime, message)], oldest first. Several threads per invoice merge."""
    by_invoice = {}
    for thread in threads or []:
        invoice_id = str(thread.get("invoice_id") or "").strip()
        if not invoice_id:
            continue
        for message in thread.get("messages") or []:
            by_invoice.setdefault(invoice_id, []).append((to_datetime(message.get("ts")), {
                "ts": str(message.get("ts") or ""),
                "user": str(message.get("user") or ""),
                "text": str(message.get("text") or ""),
            }))
    for messages in by_invoice.values():
        messages.sort(key=lambda pair: pair[0] or datetime.min)
    return by_invoice


def load_agent_touches(actions_dir):
    """invoice_id -> date of the agent's approved or edited package, from <actions>/<id>/status.json."""
    touches = {}
    if not Path(actions_dir).is_dir():
        return touches
    for entry in os.scandir(actions_dir):
        path = Path(entry.path) / "status.json"
        try:
            status = json.loads(path.read_text(encoding="utf-8-sig"))
            modified = path.stat().st_mtime
        except (OSError, ValueError):
            continue  # no package yet, or role C is mid-write; the next run sees it
        if not isinstance(status, dict) or str(status.get("state") or "").strip().lower() not in AGENT_TOUCH_STATES:
            continue
        when = to_datetime(status.get("at"))
        touches[entry.name] = when.date() if when else date.fromtimestamp(modified)
    return touches


def last_touch(sf_date, sf_channel, notes, slack_date, agent_date=None):
    """Newest touch across the sources. On the same day: Salesforce, agent, sheet, Slack."""
    candidates = []
    if sf_date:
        candidates.append((sf_date, TOUCH_RANK["salesforce"], "salesforce", sf_channel or "unknown"))
    if agent_date:
        candidates.append((agent_date, TOUCH_RANK["agent"], "agent", "agent"))
    if notes and notes[-1][0]:
        candidates.append((notes[-1][0], TOUCH_RANK["sheet"], "sheet", note_channel(notes[-1][1]["note"])))
    if slack_date:
        candidates.append((slack_date, TOUCH_RANK["slack"], "slack", "slack"))
    if not candidates:
        return None, dict(NO_TOUCH)
    when, _, source, channel = max(candidates)
    return when, {"date": when.isoformat(), "channel": channel, "source": source}


def find_conflicts(status, sf_date, sheet_date, notes):
    conflicts = []
    if sheet_date and sheet_date != sf_date:
        said = sf_date.isoformat() if sf_date else "never touched"
        conflicts.append(f"sheet says touched {sheet_date.isoformat()}, salesforce says {said}")
    if status != "disputed" and any(note["dispute_flag"] for _, note in notes):
        conflicts.append(f"sheet flags a dispute, salesforce status is {status or 'blank'}")
    return conflicts


def triage(policy, invoices, notes_by_invoice, slack_by_invoice, agent_touches=None):
    """Apply the queue rule. Returns (items in queue order, counts)."""
    agent_touches = agent_touches or {}
    as_of = policy["as_of"]
    min_days = policy["min_days_past_due"]
    stale_days = policy["stale_touch_days"]
    writeoff_max = to_cents(policy["write_off_max_amount"])
    agent_max = to_cents(policy["agent_max_amount"])
    writeoff_reason = "amount_under_" + plain_number(policy["write_off_max_amount"])
    agent_reason = "amount_under_" + plain_number(policy["agent_max_amount"])

    rows = {}
    for row in invoices:  # a repeated invoice_id keeps its last row
        invoice_id = str(row.get("invoice_id") or "").strip()
        if invoice_id:
            rows[invoice_id] = row
    counts = dict.fromkeys((
        "invoices", "open", "open_cents", "under_agent_max", "past_due_under_agent_max",
        "never_touched_queued", "conflicts", "held_back", "held_back_by_agent", "bad_rows",
        "writeoff", "agent", "escalate", "writeoff_cents", "agent_cents", "escalate_cents",
    ), 0)
    counts["duplicate_rows"] = len(invoices) - len(rows)
    queued = {bucket: [] for bucket in BUCKETS}

    for invoice_id, row in rows.items():
        counts["invoices"] += 1
        status = str(row.get("status") or "").strip().lower()
        if status == "paid":
            continue
        try:
            cents = to_cents(row.get("amount"))
        except ValueError:
            counts["bad_rows"] += 1
            continue
        counts["open"] += 1
        counts["open_cents"] += cents
        small = 0 < cents <= agent_max  # a zero or credit balance has nothing to collect
        counts["under_agent_max"] += small

        notes = notes_by_invoice.get(invoice_id, [])
        messages = slack_by_invoice.get(invoice_id, [])
        sf_date = to_date(row.get("last_touch_date"))
        sheet_date = notes[-1][0] if notes else None
        slack_date = messages[-1][0].date() if messages and messages[-1][0] else None
        conflicts = find_conflicts(status, sf_date, sheet_date, notes)
        counts["conflicts"] += bool(conflicts)

        due = to_date(row.get("due_date"))
        if due is None:
            counts["bad_rows"] += 1
            continue
        days_past_due = (as_of - due).days
        if not small or days_past_due < min_days:
            continue
        counts["past_due_under_agent_max"] += 1

        touched, touch = last_touch(sf_date, str(row.get("last_touch_channel") or "").strip(),
                                    notes, slack_date, agent_touches.get(invoice_id))
        days_since = None if touched is None else (as_of - touched).days
        if days_since is not None and days_since < stale_days:
            if sf_date is None or (as_of - sf_date).days >= stale_days:
                # Salesforce alone would have chased it again
                counts["held_back_by_agent" if touch["source"] == "agent" else "held_back"] += 1
            continue

        disputed = status == "disputed"
        sheet_dispute = any(note["dispute_flag"] for _, note in notes)
        if cents <= writeoff_max:
            bucket = "writeoff"
        elif disputed or sheet_dispute:
            bucket = "escalate"
        else:
            bucket = "agent"
        reasons = [
            f"past_due_{days_past_due}_days",
            "never_touched" if days_since is None else f"no_touch_{days_since}_days",
            writeoff_reason if bucket == "writeoff" else agent_reason,
        ]
        if disputed:
            reasons.append("status_disputed")
        if sheet_dispute:
            reasons.append("sheet_dispute_flag")

        counts[bucket] += 1
        counts[bucket + "_cents"] += cents
        counts["never_touched_queued"] += days_since is None
        queued[bucket].append((cents, invoice_id, {
            "invoice_id": invoice_id,
            "bucket": bucket,
            "reasons": reasons,
            "days_past_due": days_past_due,
            "days_since_touch": days_since,
            "invoice": {key: str(value or "").strip() for key, value in row.items() if key},
            "last_touch": touch,
            "notes": [note for _, note in notes],
            "slack": [message for _, message in messages],
            "conflicts": conflicts,
        }))

    items = []
    for bucket in BUCKETS:
        items += [item for _, _, item in sorted(queued[bucket], key=lambda t: (-t[0], t[1]))]
    return items, counts


def dollars(cents):
    return round(cents / 100, 2)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
        return reader.fieldnames or [], list(reader)


def pick_source(explicit=None, inbox=INBOX, exports=EXPORTS):
    """The inbox wins when it holds a Salesforce export. Otherwise the generated exports."""
    if explicit:
        return Path(explicit)
    if (inbox / SALESFORCE).is_file():
        return inbox
    if inbox.is_dir() and any(not p.name.startswith(".") for p in inbox.iterdir()):
        warn(f"{inbox} has files but no {SALESFORCE}, reading {exports}")
    return exports


def shown(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path, text):
    """Write through a temp file and rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)  # mkstemp makes it owner-only; the dashboard may run as another user
    os.replace(tmp, path)


def queue_json(queue):
    """Pretty header, then one item per line: valid JSON, quick to write, easy to grep."""
    head = {key: value for key, value in queue.items() if key != "items"}
    lines = [json.dumps(item) for item in queue["items"]]
    body = "[\n" + ",\n".join(lines) + "\n]" if lines else "[]"
    return json.dumps(head, indent=2)[:-2] + ',\n  "items": ' + body + "\n}\n"


def run(source, out_dir=OUT, started=None, actions_dir=None):
    """Triage the export in `source`, write queue.json and headline.json, return both.
    Agent touches come from <out_dir>/actions/ unless actions_dir says otherwise."""
    started = time.perf_counter() if started is None else started
    source, out_dir = Path(source), Path(out_dir)
    actions_dir = out_dir / "actions" if actions_dir is None else Path(actions_dir)
    if not (source / SALESFORCE).is_file():
        raise FileNotFoundError(f"no {SALESFORCE} in {source}")
    policy = load_policy(source / POLICY)
    snapshot_id, exported_on = load_manifest(source / MANIFEST)
    if exported_on:
        policy["as_of"] = exported_on  # the snapshot date drives all age math
    columns, invoices = read_csv(source / SALESFORCE)
    missing = [name for name in REQUIRED if name not in columns]
    if missing:
        raise ValueError(f"{SALESFORCE} is missing columns: {', '.join(missing)}")
    notes = index_notes(read_csv(source / SHEET)[1]) if (source / SHEET).is_file() else {}
    threads = json.loads((source / SLACK).read_text(encoding="utf-8-sig")) if (source / SLACK).is_file() else []
    if isinstance(threads, dict):
        threads = threads.get("threads", [])

    items, counts = triage(policy, invoices, notes, index_slack(threads), load_agent_touches(actions_dir))
    for key in ("bad_rows", "duplicate_rows"):
        if counts[key]:
            warn(f"{counts[key]} {key.replace('_', ' ')} in {SALESFORCE}")

    as_of = policy["as_of"].isoformat()
    queued_cents = sum(counts[b + "_cents"] for b in BUCKETS)
    totals = {
        "invoices": counts["invoices"],
        "open": counts["open"],
        "under_agent_max": counts["under_agent_max"],
        "queued": len(items),
        "writeoff": counts["writeoff"],
        "agent": counts["agent"],
        "escalate": counts["escalate"],
        "dollars_queued": dollars(queued_cents),
    }
    queue = {
        "as_of": as_of,
        "snapshot_id": snapshot_id,
        "source": shown(source),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy": dict(policy, as_of=as_of),
        "totals": totals,
        "items": items,
    }
    write_json(out_dir / "queue.json", queue_json(queue))

    handed_back = counts["writeoff"] + counts["agent"]
    elapsed = time.perf_counter() - started
    headline = {
        "as_of": as_of,
        "snapshot_id": snapshot_id,
        "source": shown(source),
        "invoices": counts["invoices"],
        "open": counts["open"],
        "dollars_open": dollars(counts["open_cents"]),
        "under_agent_max": counts["under_agent_max"],
        "under_agent_max_pct_of_open": round(100 * counts["under_agent_max"] / max(counts["open"], 1), 1),
        "past_due_under_agent_max": counts["past_due_under_agent_max"],
        "queued": len(items),
        "dollars_queued": dollars(queued_cents),
        "never_touched_queued": counts["never_touched_queued"],
        "buckets": {b: {"count": counts[b], "dollars": dollars(counts[b + "_cents"])} for b in BUCKETS},
        "conflicts": counts["conflicts"],
        "held_back_by_sheet_or_slack": counts["held_back"],
        "held_back_by_agent": counts["held_back_by_agent"],
        "minutes_per_touch": MINUTES_PER_TOUCH,
        "hours_per_week": round(len(items) * MINUTES_PER_TOUCH / 60, 1),
        "hours_handed_back_per_week": round(handed_back * MINUTES_PER_TOUCH / 60, 1),
        "hours_basis": "6 minutes per touch, each queued invoice touched once a week; "
                       "handed back counts the writeoff and agent buckets",
        "triage_seconds": round(elapsed, 3),
    }
    write_json(out_dir / "headline.json", json.dumps(headline, indent=2) + "\n")
    return queue, headline, elapsed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collections triage. Writes out/queue.json and out/headline.json.")
    parser.add_argument("--input", help="folder with the export files (default: data/inbox if it has "
                                        "salesforce_invoices.csv, else data/exports)")
    parser.add_argument("--out", default=str(OUT), help="output folder (default: out/)")
    parser.add_argument("--actions", help="agent packages to count as touches (default: <out>/actions)")
    args = parser.parse_args(argv)
    try:
        queue, headline, elapsed = run(pick_source(args.input), args.out, started=_STARTED,
                                       actions_dir=args.actions)
    except (OSError, ValueError) as error:
        warn(str(error))
        return 1
    t = queue["totals"]
    symbol = "$" if queue["policy"]["currency"] == "USD" else queue["policy"]["currency"] + " "
    limit = plain_number(queue["policy"]["agent_max_amount"])
    print(f"triage {queue['as_of']} {queue['source']} snapshot {queue['snapshot_id']}: "
          f"{t['invoices']} invoices, {t['open']} open, {t['under_agent_max']} under {symbol}{limit}, "
          f"{t['queued']} queued for {symbol}{t['dollars_queued']:,.2f} "
          f"(writeoff {t['writeoff']}, agent {t['agent']}, escalate {t['escalate']}), "
          f"{headline['conflicts']} conflicts, "
          + (f"{headline['held_back_by_agent']} held back by agent touches, " if headline["held_back_by_agent"] else "")
          + f"{elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
