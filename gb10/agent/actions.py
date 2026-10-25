#!/usr/bin/env python3
"""Draft the collections package for each queued invoice with the local model.

Reads out/queue.json (role B) and MODEL_BASE_URL / MODEL_NAME from platform/model.env
(role A). For each invoice it writes out/actions/<invoice_id>/ as CONTRACT.md lists,
records the case in agent/crm_mock/crm.sqlite, posts the package through channel/post.py
when a channel is set up, and appends one run record to out/summary.json.

    python3 agent/actions.py --limit 5
    python3 agent/actions.py --dry-run --queue agent/fixtures/queue.sample.json --limit 6

Stdlib only.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta
from http.client import HTTPException
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "agent" / "prompts"
CRM_PATH = ROOT / "agent" / "crm_mock" / "crm.sqlite"
OUT_DIR = ROOT / "out"
ACTIONS_DIR = OUT_DIR / "actions"
SUMMARY_PATH = OUT_DIR / "summary.json"
DEFAULT_QUEUE = OUT_DIR / "queue.json"
DEFAULT_MODEL_ENV = ROOT / "platform" / "model.env"
POST_PY = ROOT / "channel" / "post.py"
POLICY_FILES = (ROOT / "data" / "inbox" / "policy.yaml", ROOT / "data" / "exports" / "policy.yaml")

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3:8b"
DRY_RUN_MODEL = "dry-run"
TIMEOUT_S = 60
TEMPERATURE = 0.2
COMBINED_MAX_TOKENS = 2500
SINGLE_MAX_TOKENS = 1200
SIGN_AS = "Accounts Receivable"
SLACK_MAX_CHARS = 599  # contract: under 600
APPROVER_LINE = "Approver: ____________________  Date: ____________"
DEFAULT_POLICY = {"write_off_max_amount": 25.0, "min_days_past_due": 30, "stale_touch_days": 8, "currency": "USD"}

BUCKET_ORDER = ("agent", "writeoff", "escalate")
BUCKET_ACTION = {"agent": "outreach", "writeoff": "writeoff", "escalate": "escalate"}
BUCKET_MEANING = {
    "agent": "we ask the customer for payment",
    "escalate": "the customer disputed the invoice and a person reviews it",
    "writeoff": "a small, aged balance we propose to write off under policy",
}
ARTIFACTS = ("customer_email", "customer_slack", "internal_note", "writeoff_memo")
PACKAGE_FILES = (
    "customer_email.md",
    "customer_slack.md",
    "internal_note.md",
    "writeoff_memo.md",
    "salesforce_case.json",
    "status.json",
)
INVOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

JSON_KEYS = {
    "customer_email": [
        '"customer_email_subject": the subject line only',
        '"customer_email_body": the email body, from the greeting to the sign-off',
    ],
    "customer_slack": ['"customer_slack": the Slack message'],
    "internal_note": ['"internal_note": the note to the account owner'],
    "writeoff_memo": ['"writeoff_memo": the write-off memo'],
}
SINGLE_INSTRUCTION = {
    "customer_email": "Write the customer_email for this invoice, using the shape for its bucket. "
    "Output only the email: the subject line first, then a blank line, then the body.",
    "customer_slack": "Write the customer_slack message for this invoice, using the shape for its bucket. "
    "Output only the message.",
    "internal_note": "Write the internal_note to the account owner for this invoice. Output only the note.",
    "writeoff_memo": "Write the writeoff_memo for this invoice. Output only the memo.",
}

CRM_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    account_name TEXT,
    amount REAL,
    due_date TEXT,
    status TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    case_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT,
    case_type TEXT,
    summary TEXT,
    next_action TEXT,
    next_action_date TEXT,
    drafted_at TEXT,
    model TEXT,
    state TEXT
);
CREATE INDEX IF NOT EXISTS cases_by_invoice ON cases (invoice_id);
"""


class ModelUnreachable(Exception):
    """The endpoint is down or the model is missing. Stop the run, the next cron retries."""


class ModelError(Exception):
    """One call failed or came back unusable. Skip this invoice, the next run retries."""


# ---------------------------------------------------------------- small helpers

def log(message):
    print(message, flush=True)


def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def configure_paths(out_dir=None, crm_path=None):
    """Point output and CRM paths somewhere else (tests use a temp folder)."""
    global OUT_DIR, ACTIONS_DIR, SUMMARY_PATH, CRM_PATH
    if out_dir is not None:
        OUT_DIR = Path(out_dir)
        ACTIONS_DIR = OUT_DIR / "actions"
        SUMMARY_PATH = OUT_DIR / "summary.json"
    if crm_path is not None:
        CRM_PATH = Path(crm_path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_env_file(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}" if d else "unknown"


def business_day_on_or_after(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def to_float(value, default=0.0):
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return default


def money(amount, currency="USD"):
    currency = (currency or "USD").upper()
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def item_id(item):
    inv = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    return str(item.get("invoice_id") or inv.get("invoice_id") or "").strip()


def item_bucket(item):
    return item.get("bucket") if item.get("bucket") in BUCKET_ACTION else "agent"


def item_amount(item):
    inv = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    return to_float(inv.get("amount", item.get("amount")))


def action_id_for(item, snapshot_id):
    return f"{item_id(item)}:{BUCKET_ACTION[item_bucket(item)]}:{snapshot_id}"


# ---------------------------------------------------------------- config

def load_model_config(path):
    values = read_env_file(path) or {}
    base = (values.get("MODEL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    if urllib.parse.urlparse(base).path in ("", "/"):
        base += "/v1"
    name = values.get("MODEL_NAME") or DEFAULT_MODEL
    return {"base_url": base, "name": name}


def load_policy(queue=None):
    """Policy values: the queue's policy block, else policy.yaml from the inbox, else CONTRACT.md defaults."""
    policy = dict(DEFAULT_POLICY)
    block = queue.get("policy") if isinstance(queue, dict) else None
    if isinstance(block, dict):
        for key, value in block.items():
            if key.endswith("_amount"):
                policy[key] = to_float(value, policy.get(key, 0.0))
            elif key.endswith("_days") or key.endswith("_past_due"):
                try:
                    policy[key] = int(float(value))
                except (TypeError, ValueError):
                    pass
            else:
                policy[key] = value
        return policy
    for path in POLICY_FILES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r"\s*([A-Za-z_]+)\s*:\s*([^#]*)", line)
            if not m or not m.group(2).strip():
                continue
            key, value = m.group(1), m.group(2).strip().strip("'\"")
            if key.endswith("_amount"):
                policy[key] = to_float(value, policy.get(key, 0.0))
            elif key.endswith("_days") or key.endswith("_past_due"):
                try:
                    policy[key] = int(float(value))
                except ValueError:
                    pass
            else:
                policy[key] = value
        break
    return policy


def load_prompts():
    return {name: (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
            for name in ("system",) + ARTIFACTS}


def prompt_for_bucket(text, bucket):
    """Keep the shape and examples for this bucket only. Small models copy lines from other buckets' examples."""
    keep = []
    for section in re.split(r"(?m)^(?=## )", text):
        shape = re.match(r"## Shape when the bucket is (\w+)", section)
        example = re.match(r"## Example \d+, (\w+) bucket", section)
        if (shape and shape.group(1) != bucket) or (example and example.group(1) != bucket):
            continue
        keep.append(section)
    return "".join(keep).strip()


# ---------------------------------------------------------------- facts

def reason_number(reasons, pattern):
    for reason in reasons:
        m = re.search(pattern, reason)
        if m:
            return int(float(m.group(1)))
    return None


def reason_thresholds(reasons):
    found = []
    for reason in reasons:
        m = re.match(r"^(?:amount|write_?off)\w*?_(\d+(?:\.\d+)?)$", reason)
        if m:
            found.append(float(m.group(1)))
    return found


def source_label(source, channel):
    labels = {"salesforce": "Salesforce", "collector_sheet": "collector sheet", "sheet": "collector sheet",
              "slack": "Slack", "slack_threads": "Slack"}
    src = labels.get(str(source).lower(), str(source))
    chan = str(channel or "").strip()
    if not chan or chan.lower() == str(source).lower() or chan.lower() == src.lower():
        return src
    return f"{chan}, {src}" if src else chan


def build_facts(item, meta, policy):
    inv = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    reasons = [str(r).strip().lower() for r in (item.get("reasons") or [])]
    as_of = meta["as_of"]
    bucket = item_bucket(item)
    amount = item_amount(item)
    currency = str(inv.get("currency") or policy.get("currency") or "USD").strip().upper() or "USD"
    due = parse_date(inv.get("due_date"))

    days_past_due = reason_number(reasons, r"^past_due_(\d+)(?:_days?)?$")
    if days_past_due is None and due:
        days_past_due = (as_of - due).days

    touch = item.get("last_touch") if isinstance(item.get("last_touch"), dict) else {}
    touch_date = parse_date(touch.get("date"))
    touch_channel, touch_source = touch.get("channel") or "", touch.get("source") or ""
    if not touch_date and parse_date(inv.get("last_touch_date")):
        touch_date = parse_date(inv.get("last_touch_date"))
        touch_channel, touch_source = inv.get("last_touch_channel") or "", "salesforce"
    days_since_touch = reason_number(reasons, r"^no_touch_(\d+)(?:_days?)?$")
    if days_since_touch is None and touch_date:
        days_since_touch = (as_of - touch_date).days
    if touch_date:
        where = source_label(touch_source, touch_channel)
        logged = source_label(touch_source, "")
        how = f"{touch_channel} (logged in {logged})" if touch_channel and logged and where != logged else where
        touch_text = f"{long_date(touch_date)}, {how}" + (f", {days_since_touch} days ago" if days_since_touch is not None else "")
        touch_short = f"{long_date(touch_date)} ({where})"
    else:
        touch_text, touch_short = "no touch on record", "none on record"

    thresholds = [t for t in reason_thresholds(reasons) if t >= amount]
    write_off_max = min(thresholds) if bucket == "writeoff" and thresholds else float(policy["write_off_max_amount"])
    min_days = reason_number(reasons, r"^(?:min_days_past_due|past_due_(?:over|gte|ge|min|at_least))_(\d+)$")
    if min_days is None:
        min_days = int(policy["min_days_past_due"])

    notes = sorted((n for n in (item.get("notes") or []) if isinstance(n, dict) and n.get("note")),
                   key=lambda n: str(n.get("note_date") or ""))
    note_lines = []
    for n in notes[-3:]:
        line = f'{long_date(parse_date(n.get("note_date")))}, {n.get("collector") or "collector"}: "{clip(n.get("note"), 220)}"'
        if parse_date(n.get("promise_to_pay_date")):
            line += f" (promise to pay {long_date(parse_date(n.get('promise_to_pay_date')))})"
        if str(n.get("dispute_flag")).lower() == "true":
            line += " (marked disputed)"
        note_lines.append(line)
    slack = sorted((m for m in (item.get("slack") or []) if isinstance(m, dict) and m.get("text")),
                   key=lambda m: str(m.get("ts") or ""))
    slack_lines = [f'{long_date(parse_date(m.get("ts")))}, {m.get("user") or "someone"}: "{clip(m.get("text"), 220)}"'
                   for m in slack[-3:]]
    conflicts = [clip(c, 200) for c in (item.get("conflicts") or []) if c]
    disputed = [n for n in notes if str(n.get("dispute_flag")).lower() == "true"]
    dispute_source = disputed[-1]["note"] if disputed else (slack[-1]["text"] if slack else (notes[-1]["note"] if notes else ""))
    dispute_text = clip(dispute_source, 220)

    contact = str(inv.get("contact_name") or "").strip()
    owner = str(inv.get("account_owner") or "").strip()
    amount_str = money(amount, currency)
    if bucket == "agent":
        need = f"payment of {amount_str}, or a reply with the date they will pay"
    elif bucket == "escalate":
        need = "the one document or detail that settles the dispute"
    else:
        need = "no payment; ask them not to send one, and to reply only if their records differ"

    return {
        "invoice_id": item_id(item),
        "bucket": bucket,
        "action_id": action_id_for(item, meta["snapshot_id"]),
        "snapshot_id": meta["snapshot_id"],
        "as_of": as_of,
        "customer": str(inv.get("account_name") or "the customer").strip(),
        "contact_name": contact,
        "contact_first": contact.split()[0] if contact else "there",
        "contact_email": str(inv.get("contact_email") or "").strip(),
        "contact_slack": str(inv.get("contact_slack") or "").strip(),
        "owner_name": owner,
        "owner_first": owner.split()[0] if owner else "team",
        "amount": amount,
        "currency": currency,
        "amount_str": amount_str,
        "invoice_date": parse_date(inv.get("invoice_date")),
        "due_date": due,
        "days_past_due": days_past_due if days_past_due is not None else 0,
        "status": str(inv.get("status") or "open"),
        "touch_date": touch_date,
        "touch_text": touch_text,
        "touch_short": touch_short,
        "days_since_touch": days_since_touch,
        "notes": note_lines,
        "last_note": clip(notes[-1]["note"], 220) if notes else "",
        "slack": slack_lines,
        "dispute_text": dispute_text,
        "conflicts": conflicts,
        "need": need,
        "reply_by": business_day_on_or_after(as_of + timedelta(days=7)),
        "next_business_day": business_day_on_or_after(as_of + timedelta(days=1)),
        "write_off_max": write_off_max,
        "min_days_past_due": min_days,
    }


def facts_text(f):
    reach = ", ".join(x for x in (f["contact_email"], f"Slack {f['contact_slack']}" if f["contact_slack"] else "") if x)
    status = f["status"]
    if status == "partially_paid":
        status += " (part paid, the amount open is the remaining balance)"
    lines = [
        f"Bucket: {f['bucket']} ({BUCKET_MEANING[f['bucket']]})",
        f"Invoice: {f['invoice_id']}",
        f"Customer: {f['customer']}",
        f"Contact: {f['contact_name'] or 'unknown'} (first name {f['contact_first']})" + (f", {reach}" if reach else ""),
        f"Account owner: {f['owner_name'] or 'unassigned'} (first name {f['owner_first']})",
        f"Amount open: {f['amount_str']}",
        f"Invoice date: {long_date(f['invoice_date'])}",
        f"Due date: {long_date(f['due_date'])}",
        f"Days past due: {f['days_past_due']}",
        f"Salesforce status: {status}",
        f"Last touch: {f['touch_text']}",
        "Collector notes: " + ("; ".join(f["notes"]) or "none"),
        "Slack messages: " + ("; ".join(f["slack"]) or "none"),
        "Data conflicts: " + ("; ".join(f["conflicts"]) or "none"),
        f"What we need: {f['need']}",
        f"Reply by: {long_date(f['reply_by'])}",
        f"Today: {long_date(f['as_of'])}",
        f"Sign as: {SIGN_AS}",
    ]
    if f["bucket"] == "writeoff":
        lines.append(f"Write-off policy (policy.yaml): write_off_max_amount {money(f['write_off_max'], f['currency'])}, "
                     f"min_days_past_due {f['min_days_past_due']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- model call and parsing

def chat(cfg, system, user, max_tokens, json_mode, stats, plain=False):
    name = cfg["name"]
    lower = name.lower()
    body = {
        "model": name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user + ("\n/no_think" if lower.startswith("qwen3") else "")},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if not plain:
        # Thinking off. Reasoning tokens count against max_tokens and can leave the answer empty, and they
        # cost seconds per draft. Ollama reads reasoning_effort, vLLM reads chat_template_kwargs.
        # gpt-oss cannot switch reasoning off, so it gets the lowest setting.
        body["reasoning_effort"] = "low" if "gpt-oss" in lower else "none"
        if "qwen" in lower:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    stats["calls"] += 1
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        exc.close()
        if exc.code == 404:
            raise ModelUnreachable(f"HTTP 404 from {cfg['base_url']} for model {name}: {detail}")
        if not plain:  # the server refused an optional field, ask again with a plain request
            return chat(cfg, system, user, max_tokens, json_mode, stats, plain=True)
        raise ModelError(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            raise ModelError(f"no answer within {TIMEOUT_S}s")
        raise ModelUnreachable(f"model endpoint unreachable at {cfg['base_url']}: {exc.reason}")
    except socket.timeout:
        raise ModelError(f"no answer within {TIMEOUT_S}s")
    except (OSError, HTTPException) as exc:
        raise ModelError(f"connection dropped: {exc}")
    except ValueError:
        raise ModelError("the endpoint answered with something that is not JSON")
    try:
        message = payload["choices"][0]["message"]
        content = message.get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ModelError("unexpected response shape")
    if not content.strip() and (message.get("reasoning") or message.get("reasoning_content")):
        raise ModelError("the model spent its token budget reasoning and returned no answer")
    return content


THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n?(.*?)```", re.S)


def strip_think(text):
    text = THINK_RE.sub("", text or "")
    low = text.lower()
    if "<think>" in low:  # unclosed reasoning block, nothing usable after it
        text = text[: low.index("<think>")]
    return text.strip()


def extract_json_object(text):
    """Find the JSON object in a model reply: strip reasoning and code fences, then first { to last }."""
    text = strip_think(text)
    candidates = [m.group(1) for m in FENCE_RE.finditer(text)] + [text]
    for chunk in candidates:
        start, end = chunk.find("{"), chunk.rfind("}")
        if start == -1 or end <= start:
            continue
        for strict in (True, False):  # strict=False accepts raw newlines inside strings
            try:
                obj = json.loads(chunk[start: end + 1], strict=strict)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def clean_text(text):
    text = strip_think(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    fenced = re.fullmatch(r"\s*```[A-Za-z0-9_-]*\n(.*?)\n?```\s*", text, re.S)
    if fenced:
        text = fenced.group(1)
    text = re.sub(r"[ \t]*\u2014[ \t]*", ", ", text)  # em dash
    text = re.sub(r"[ \t]*\u2013[ \t]*", "-", text)  # en dash
    text = re.sub(r"\bI hope (?:this|the|my) (?:e-?mail|message|note) finds you (?:well|doing well)[.!,]?[ \t]*",
                  "", text, flags=re.I)
    text = re.sub(r",[ \t]*,", ",", text)
    text = re.sub(r",[ \t]*([.?!])", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_subject(text):
    lines = [line for line in clean_text(text).split("\n") if line.strip()]
    line = lines[0] if lines else ""
    line = re.sub(r"^\W*(?:subject|subj)\s*:\s*", "", line, flags=re.I)
    line = line.strip().strip("*").strip().strip('"').strip()
    return line[:150]


def split_email(text, facts):
    lines = clean_text(text).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    if re.match(r"^(hi|hello|dear)\b", lines[0].strip(), re.I):  # no subject line came back
        return template_subject(facts), "\n".join(lines).strip()
    return clean_subject(lines[0]), "\n".join(lines[1:]).strip()


def as_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(v) for v in value if isinstance(v, (str, int, float)))
    return ""


def texts_from_json(obj, names, facts):
    norm = {re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v for k, v in obj.items()}
    texts = {}
    if "customer_email" in names:
        nested = norm.get("customer_email")
        if isinstance(nested, dict):
            subject, body = as_text(nested.get("subject")), as_text(nested.get("body"))
        elif isinstance(nested, str) and nested.strip():
            subject, body = split_email(nested, facts)
        else:
            subject = as_text(norm.get("customer_email_subject") or norm.get("email_subject") or norm.get("subject"))
            body = as_text(norm.get("customer_email_body") or norm.get("email_body") or norm.get("body"))
        body = re.sub(r"(?i)^\s*subject\s*:[^\n]*\n+", "", clean_text(body))
        if subject or body:
            texts["customer_email"] = (clean_subject(subject), body)
    for name in names:
        if name != "customer_email":
            value = clean_text(as_text(norm.get(name)))
            if value:
                texts[name] = value
    return texts


def check_artifact(name, value, facts):
    """None when the draft is usable, else a short reason."""
    invoice = facts["invoice_id"]
    if name == "customer_email":
        subject, body = value
        if not subject or len(body) < 40:
            return "empty"
        return None if invoice in subject or invoice in body else "no invoice number"
    if not value or len(value) < 30:
        return "empty"
    return None if invoice in value else "no invoice number"


def json_instruction(names):
    keys = [key for name in names for key in JSON_KEYS[name]]
    return ("Write every draft for this invoice, using the shape for its bucket.\n"
            "Return one JSON object and nothing else. Use exactly these keys:\n- " + "\n- ".join(keys) +
            "\nEach value is plain text. Put line breaks inside a value as \\n.")


def artifact_names(bucket):
    names = ["customer_email", "customer_slack", "internal_note"]
    if bucket == "writeoff":
        names.append("writeoff_memo")
    return names


def draft_with_model(cfg, prompts, facts):
    """One call for every artifact as JSON; one call per artifact for anything missing or unusable."""
    names = artifact_names(facts["bucket"])
    bucket = facts["bucket"]
    facts_block = "Facts:\n" + facts_text(facts)
    stats = {"calls": 0}
    notes = []
    texts = {}
    system = "\n\n".join([prompts["system"]] + [prompt_for_bucket(prompts[n], bucket) for n in names])
    try:
        raw = chat(cfg, system, facts_block + "\n\n" + json_instruction(names), COMBINED_MAX_TOKENS, True, stats)
        obj = extract_json_object(raw)
        if obj is None:
            notes.append("reply had no JSON object")
        else:
            texts = texts_from_json(obj, names, facts)
    except ModelError as exc:
        notes.append(str(exc))

    for name in names:
        problem = check_artifact(name, texts[name], facts) if name in texts else "missing"
        if problem is None:
            continue
        notes.append(f"{name} {problem}, asked again")
        system_one = prompts["system"] + "\n\n" + prompt_for_bucket(prompts[name], bucket)
        try:
            raw = chat(cfg, system_one, facts_block + "\n\n" + SINGLE_INSTRUCTION[name], SINGLE_MAX_TOKENS, False, stats)
        except ModelError as exc:
            raise ModelError(f"{name}: {exc}")
        value = split_email(raw, facts) if name == "customer_email" else clean_text(raw)
        problem = check_artifact(name, value, facts)
        if problem == "empty":
            raise ModelError(f"{name}: empty draft after a second call")
        if problem:
            notes.append(f"{name} kept with {problem}")
        texts[name] = value
    method = "1 call" if stats["calls"] == 1 else f"{stats['calls']} calls"
    if notes:
        method += "; " + "; ".join(notes)
    return finalize(texts, facts), method, stats["calls"]


# ---------------------------------------------------------------- deterministic text

def template_subject(f):
    if f["bucket"] == "escalate":
        return f"Your question on invoice {f['invoice_id']} for {f['amount_str']}"
    if f["bucket"] == "writeoff":
        return f"Balance of {f['amount_str']} on invoice {f['invoice_id']} is closed"
    return f"Invoice {f['invoice_id']} for {f['amount_str']} is {f['days_past_due']} days past due"


def policy_clause(f):
    meets = f["amount"] <= f["write_off_max"] + 1e-9 and f["days_past_due"] >= f["min_days_past_due"]
    verdict = "This invoice meets both." if meets else "Check before approving: this invoice does not meet both."
    return (f"Policy clause: policy.yaml allows a write-off when the amount is at or under write_off_max_amount "
            f"({money(f['write_off_max'], f['currency'])}) and the invoice is at least min_days_past_due "
            f"({f['min_days_past_due']}) days past due. {verdict}")


def memo_reason(f, memo):
    """The model's Reason line, minus any sentence that contradicts the touch or note facts."""
    m = re.search(r"(?im)^\s*reason\s*:\s*(.+)$", memo or "")
    if not m:
        return None
    keep = []
    for sentence in re.split(r"(?<=[.!?])\s+", m.group(1).strip()):
        low = sentence.lower()
        if f["touch_date"] and re.search(r"no touch|never (?:been )?touched|not been touched|no contact", low):
            continue
        if not f["last_note"] and "collector note:" in low:
            continue
        keep.append(sentence)
    return " ".join(keep).strip() or None


def memo_template(f, reason=None):
    """Every line but Reason comes from the facts, so the audit memo cannot carry a wrong amount, date or clause."""
    if reason:
        pass
    elif f["last_note"]:
        reason = f'Collector note: "{f["last_note"].rstrip(".")}".'
    elif f["touch_date"] is None:
        reason = "No collector note on record. The balance has had no touch since it was issued."
    else:
        reason = "No collector note on record."
    return "\n".join([
        f"Write-off memo: {f['invoice_id']}, {f['customer']}",
        f"Amount: {f['amount_str']}",
        f"Due date: {long_date(f['due_date'])} ({f['days_past_due']} days past due)",
        f"Last touch: {f['touch_short']}",
        policy_clause(f),
        f"Reason: {reason}",
        f"Recommendation: Write off {f['amount_str']} and close the balance in Salesforce.",
        f"Prepared by: Collections agent, {long_date(f['as_of'])}, draft for review",
        APPROVER_LINE,
    ])


def template_texts(f):
    """The --dry-run drafts: the same shapes as the prompts, filled from the facts with no model."""
    inv, amt, dpd = f["invoice_id"], f["amount_str"], f["days_past_due"]
    first, contact = f["contact_first"], f["contact_name"] or "the customer contact"
    due, reply = long_date(f["due_date"]), long_date(f["reply_by"])
    owner = f["owner_name"] or "the account owner"
    opener = f"Invoice {inv} for {amt} was due on {due} and is now {dpd} days past due."
    if f["bucket"] == "agent":
        partial = f["status"] == "partially_paid"
        ack = " Thank you for the partial payment on this invoice." if partial else ""
        ask = f"the remaining {amt}" if partial else f"payment of {amt}"
        body = (f"Hi {first},\n\n{opener}{ack}\n\n"
                f"Could you send {ask}, or reply with the date you expect to pay, by {reply}? "
                "If you have already paid, please reply with the payment date and reference so we can match it.\n\n"
                f"Thank you,\n{SIGN_AS}")
        slack = (f"Hi {first}, invoice {inv} for {amt} was due {due} and is now {dpd} days past due.{ack} "
                 f"Could you send {ask if partial else 'payment'} or a pay date by {reply}? "
                 "If it is already paid, the payment date and reference help us match it.")
        asked = f"We drafted an email and a Slack message to {contact} asking for payment or a pay date by {reply}."
        need = f"If you speak with {f['customer']} this week, a quick mention of the invoice would help."
        question = f"Is {contact} still the right AP contact at {f['customer']}?"
    elif f["bucket"] == "escalate":
        dispute = f'You told us: "{f["dispute_text"]}"' if f["dispute_text"] else "You raised a question about this invoice."
        body = (f"Hi {first},\n\n{opener}\n\n"
                f"{dispute} We have paused reminders on this invoice while {owner} reviews it.\n\n"
                f"Could you send the document that settles it, such as a delivery receipt or packing slip, by {reply}?\n\n"
                f"Thank you,\n{SIGN_AS}")
        slack = (f"Hi {first}, thanks for flagging the issue on invoice {inv} for {amt} (due {due}, {dpd} days past due). "
                 f"We have paused reminders while {owner} reviews it. "
                 f"Could you send the document that settles it by {reply}?")
        asked = f"We paused reminders and asked {contact} for the document that settles the dispute by {reply}."
        need = "We need your call on a credit or a corrected invoice before anyone contacts them again."
        question = f"Should {inv} get a credit or a corrected invoice?"
    else:
        body = (f"Hi {first},\n\n{opener}\n\n"
                "We have closed this balance, so no payment is needed. Please do not send a payment for this invoice.\n\n"
                f"If your records show something different, reply by {reply} and we will look again.\n\n"
                f"Thank you,\n{SIGN_AS}")
        slack = (f"Hi {first}, we have closed the {amt} balance on invoice {inv} (due {due}, {dpd} days past due), "
                 "so no payment is needed. Please do not send one for this invoice. "
                 f"If your records differ, let us know by {reply}.")
        asked = (f"We propose to write the balance off under policy and drafted a note telling {contact} "
                 "no payment is needed.")
        need = "Before the approver signs the memo, we need to know nothing is pending on this balance."
        question = f"Is a credit or rebill pending on {inv} that should keep it open?"
    note = (f"Hi {f['owner_first']},\n\n"
            f"{f['customer']}'s invoice {inv} for {amt} is {dpd} days past due. Last touch: {f['touch_short']}.\n"
            f"{asked}\n{need}\n\nQuestion: {question}")
    texts = {"customer_email": (template_subject(f), body), "customer_slack": slack, "internal_note": note}
    if f["bucket"] == "writeoff":
        texts["writeoff_memo"] = memo_template(f)
    return finalize(texts, f)


def limit_chars(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind(".\n"), cut.rfind("?\n"))
    if end >= limit // 2:
        return cut[: end + 1].rstrip()
    return cut[: cut.rfind(" ")].rstrip() if " " in cut else cut


NO_TOUCH_RE = re.compile(r"\bno touch\b|\bnever (?:been )?touched\b|\bnot been touched\b|\bnone on record\b", re.I)


def fix_touch_claims(text, f):
    """A sentence about touches must match the facts. Small models copy "no touch on record" from the shape."""
    parts = re.split(r"(?<=[.!?])(\s+)", text)
    for i, part in enumerate(parts):
        if not part.strip() or not NO_TOUCH_RE.search(part) or part.lstrip().lower().startswith(("hi ", "hello ")):
            continue
        if f["touch_date"]:
            parts[i] = f"The last touch was on {f['touch_short']}."
        elif part.strip().lower().startswith("the last touch was"):
            parts[i] = "There is no touch on record."
    return "".join(parts)


def finalize(texts, f):
    """Guarantees that hold whatever the model wrote."""
    texts["customer_slack"] = limit_chars(" ".join(texts["customer_slack"].split()), SLACK_MAX_CHARS)
    texts["internal_note"] = fix_touch_claims(texts["internal_note"], f)
    if "writeoff_memo" in texts:
        texts["writeoff_memo"] = memo_template(f, memo_reason(f, texts["writeoff_memo"]))
    return texts


def build_case(f, model_label, drafted_at):
    contact = f["contact_name"] or "the customer contact"
    owner = f["owner_name"] or "the account owner"
    head = f"{f['customer']}, {f['invoice_id']}, {f['amount_str']}, {f['days_past_due']} days past due, last touch {f['touch_short']}."
    if f["bucket"] == "writeoff":
        case_type = "writeoff"
        summary = (f"{head} Write-off memo drafted under policy (write_off_max_amount "
                   f"{money(f['write_off_max'], f['currency'])}, min_days_past_due {f['min_days_past_due']}).")
        next_action = f"Approver signs the write-off memo, then post the {f['amount_str']} adjustment and close the balance"
        next_date = f["next_business_day"]
    elif f["bucket"] == "escalate":
        case_type = "collections_outreach"
        summary = (f"{head} Disputed. Reminders paused; drafted a request to {contact} for the document that settles it, "
                   f"and a note to {owner}.")
        next_action = f"{owner} reviews the dispute and decides on a credit or correction before any further reminder"
        next_date = f["next_business_day"]
    else:
        case_type = "collections_outreach"
        summary = (f"{head} Drafted an email and a Slack message to {contact} asking for payment or a pay date "
                   f"by {long_date(f['reply_by'])}, and a note to {owner}.")
        next_action = (f"Send the approved email and Slack message to {contact}; follow up if there is no payment "
                       f"or reply by {long_date(f['reply_by'])}")
        next_date = f["reply_by"]
    return {
        "invoice_id": f["invoice_id"],
        "case_type": case_type,
        "summary": summary,
        "next_action": next_action,
        "next_action_date": next_date.isoformat(),
        "drafted_at": drafted_at,
        "model": model_label,
        "action_id": f["action_id"],
    }


# ---------------------------------------------------------------- package, CRM, summary

def archive_previous(folder):
    """Move an earlier package into history/ so a redraft never destroys a collector's edits."""
    present = [folder / name for name in PACKAGE_FILES if (folder / name).exists()]
    if not present:
        return None
    state = (read_json(folder / "status.json") or {}).get("state") or "unknown"
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", str(state))
    base = f"{datetime.now():%Y%m%dT%H%M%S}-{label}"
    dest = folder / "history" / base
    n = 1
    while dest.exists():
        n += 1
        dest = folder / "history" / f"{base}-{n}"
    dest.mkdir(parents=True)
    for path in present:
        os.replace(path, dest / path.name)
    return dest


def write_package(f, texts, case):
    folder = ACTIONS_DIR / f["invoice_id"]
    archive_previous(folder)
    subject, body = texts["customer_email"]
    files = {
        "customer_email.md": f"{subject}\n\n{body}\n",
        "customer_slack.md": texts["customer_slack"] + "\n",
        "internal_note.md": texts["internal_note"] + "\n",
    }
    if "writeoff_memo" in texts:
        files["writeoff_memo.md"] = texts["writeoff_memo"] + "\n"
    files["salesforce_case.json"] = json.dumps(case, indent=2, ensure_ascii=False) + "\n"
    for name, text in files.items():
        atomic_write(folder / name, text)
    # status.json last: a folder with a status file is a complete package
    write_json(folder / "status.json", {"state": "drafted", "by": "", "at": case["drafted_at"],
                                         "action_id": f["action_id"], "drafted_at": case["drafted_at"]})
    return files


def crm_connect():
    CRM_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(CRM_PATH), timeout=10)
    con.executescript(CRM_SCHEMA)
    return con


def crm_record(f, case):
    with closing(crm_connect()) as con:
        with con:
            con.execute(
                "INSERT INTO invoices (invoice_id, account_name, amount, due_date, status) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(invoice_id) DO UPDATE SET account_name = excluded.account_name, amount = excluded.amount, "
                "due_date = excluded.due_date, status = excluded.status",
                (f["invoice_id"], f["customer"], round(f["amount"], 2),
                 f["due_date"].isoformat() if f["due_date"] else "", f["status"]),
            )
            con.execute("UPDATE cases SET state = 'superseded' WHERE invoice_id = ? AND state IN ('drafted', 'edited')",
                        (f["invoice_id"],))
            cursor = con.execute(
                "INSERT INTO cases (invoice_id, case_type, summary, next_action, next_action_date, drafted_at, model, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'drafted')",
                (case["invoice_id"], case["case_type"], case["summary"], case["next_action"],
                 case["next_action_date"], case["drafted_at"], case["model"]),
            )
            return cursor.lastrowid


def review_counts():
    """Cumulative review state, read from every status.json, with approved dollars from the CRM."""
    counts = {"packages": 0, "approved": 0, "rejected": 0, "edited": 0, "awaiting_review": 0}
    if ACTIONS_DIR.is_dir():
        for folder in ACTIONS_DIR.iterdir():
            status = read_json(folder / "status.json") if folder.is_dir() else None
            if not isinstance(status, dict):
                continue
            counts["packages"] += 1
            state = status.get("state")
            if state in ("approved", "rejected", "edited"):
                counts[state] += 1
            if state in ("drafted", "edited"):
                counts["awaiting_review"] += 1
    approved = written_off = 0.0
    if CRM_PATH.exists():
        try:
            with closing(sqlite3.connect(str(CRM_PATH), timeout=10)) as con:
                rows = con.execute(
                    "SELECT c.case_type, i.amount FROM cases c JOIN invoices i ON i.invoice_id = c.invoice_id "
                    "WHERE c.case_id IN (SELECT MAX(case_id) FROM cases GROUP BY invoice_id) AND c.state = 'approved'"
                ).fetchall()
            for case_type, amount in rows:
                approved += amount or 0.0
                if case_type == "writeoff":
                    written_off += amount or 0.0
        except sqlite3.Error:
            pass
    counts["approved_dollars"] = round(approved, 2)
    counts["written_off_dollars"] = round(written_off, 2)
    return counts


def append_summary(record):
    runs = read_json(SUMMARY_PATH)
    if isinstance(runs, dict) and isinstance(runs.get("runs"), list):
        runs = runs["runs"]
    if not isinstance(runs, list):
        runs = []
    runs.append(record)
    write_json(SUMMARY_PATH, runs)


def display_path(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- selection

def package_status(invoice_id):
    folder = ACTIONS_DIR / invoice_id
    status = read_json(folder / "status.json")
    if not isinstance(status, dict):
        return None
    case = read_json(folder / "salesforce_case.json") or {}
    return dict(status, _model=case.get("model", ""))


def package_age_days(status, now):
    stamp = str(status.get("drafted_at") or status.get("at") or "").replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.astimezone()  # naive means local time
    return (now - when).total_seconds() / 86400


def needs_draft(status, action_id, now, stale_days, dry_run):
    """Skip rule. True means draft this invoice now."""
    if status is None:
        return True  # never drafted
    state = status.get("state")
    if state == "rejected":
        return True  # rejected drafts are always eligible again
    if not dry_run and state == "drafted" and status.get("_model") == DRY_RUN_MODEL:
        return True  # a dry-run placeholder waiting for the model
    old_action = str(status.get("action_id") or "").split(":")
    if len(old_action) >= 3 and old_action[1] != action_id.split(":")[1]:
        return True  # the bucket changed, for example a dispute arrived after an outreach draft
    if status.get("action_id") == action_id:
        return False  # same invoice, action and snapshot: a bundle pushed twice never drafts twice
    if state in ("drafted", "approved", "edited"):
        age = package_age_days(status, now)
        if age is not None and age < stale_days:
            return False  # drafted less than stale_touch_days ago, a new snapshot alone does not redraft
    return True


def select_items(items, snapshot_id, limit, dry_run, stale_days, now=None):
    """Never-drafted invoices first, then the rest, each group by amount descending."""
    now = now or datetime.now().astimezone()
    fresh, again = [], []
    skipped = 0
    for item in items:
        status = package_status(item_id(item))
        if not needs_draft(status, action_id_for(item, snapshot_id), now, stale_days, dry_run):
            skipped += 1
        elif status is None:
            fresh.append(item)
        else:
            again.append(item)
    ordered = sorted(fresh, key=item_amount, reverse=True) + sorted(again, key=item_amount, reverse=True)
    return (ordered if limit is None else ordered[:max(limit, 0)]), skipped


# ---------------------------------------------------------------- run

def load_poster():
    if not POST_PY.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("collections_channel_post", POST_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # the channel must never break drafting
        log(f"channel/post.py not loaded: {exc}")
        return None
    return module if hasattr(module, "post_package") else None


def acquire_lock():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(OUT_DIR / ".actions.lock", "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def resolve_queue(value):
    path = Path(value)
    if not path.is_absolute() and not path.exists() and (ROOT / path).exists():
        path = ROOT / path
    return path


def parse_args(argv):
    p = argparse.ArgumentParser(description="Draft collections packages for queued invoices with the local model.")
    p.add_argument("--limit", type=int, default=None, help="draft at most N invoices this run")
    p.add_argument("--bucket", choices=BUCKET_ORDER, help="only invoices in this bucket")
    p.add_argument("--invoice", action="append", metavar="ID", help="only this invoice id (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="templated text, no model call, model recorded as dry-run")
    p.add_argument("--queue", default=str(DEFAULT_QUEUE), help="queue file (default out/queue.json)")
    p.add_argument("--model-env", default=str(DEFAULT_MODEL_ENV),
                   help="file with MODEL_BASE_URL and MODEL_NAME (default platform/model.env)")
    p.add_argument("--post", action=argparse.BooleanOptionalAction, default=None,
                   help="post each package to the channel (default: on for model runs, off for dry runs)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    queue_path = resolve_queue(args.queue)
    queue = read_json(queue_path)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        print(f"actions.py: no queue at {queue_path}. Run python3 triage/triage.py first.", file=sys.stderr)
        return 1
    lock = acquire_lock()
    if lock is None:
        log("actions.py: another run is still drafting, skipping this one")
        return 0
    try:
        return run(args, queue_path, queue)
    finally:
        lock.close()


def run(args, queue_path, queue):
    started, run_at = time.time(), iso_now()
    meta = {"as_of": parse_date(queue.get("as_of")) or date.today(),
            "snapshot_id": str(queue.get("snapshot_id") or "local")}
    all_items = [item for item in queue["items"] if isinstance(item, dict)]
    items = all_items
    if args.bucket:
        items = [item for item in items if item_bucket(item) == args.bucket]
    if args.invoice:
        wanted = set(args.invoice)
        items = [item for item in items if item_id(item) in wanted]

    cfg = load_model_config(args.model_env)
    model_label = DRY_RUN_MODEL if args.dry_run else cfg["name"]
    prompts = None if args.dry_run else load_prompts()
    policy = load_policy(queue)
    poster = load_poster() if ((not args.dry_run) if args.post is None else args.post) else None
    stale_days = int(policy.get("stale_touch_days") or 8)
    selected, skipped = select_items(items, meta["snapshot_id"], args.limit, args.dry_run, stale_days)
    log(f"queue {display_path(queue_path)}, snapshot {meta['snapshot_id']}, {len(items)} items, "
        f"{len(selected)} to draft, {skipped} already drafted, model "
        + (DRY_RUN_MODEL if args.dry_run else f"{cfg['name']} at {cfg['base_url']}"))

    drafted, errors, calls, posted, stopped = [], 0, 0, 0, ""
    for item in selected:
        facts = build_facts(item, meta, policy)
        if not INVOICE_ID_RE.match(facts["invoice_id"]):
            log(f"skipped an item with an unusable invoice id: {facts['invoice_id']!r}")
            errors += 1
            continue
        t0 = time.time()
        try:
            if args.dry_run:
                texts, method, n_calls = template_texts(facts), "template", 0
            else:
                texts, method, n_calls = draft_with_model(cfg, prompts, facts)
        except ModelUnreachable as exc:
            errors += 1
            stopped = str(exc)
            log(f"stopped: {exc}")
            break
        except ModelError as exc:
            errors += 1
            log(f"failed {facts['invoice_id']}: {exc}")
            continue
        calls += n_calls
        case = build_case(facts, model_label, iso_now())
        files = write_package(facts, texts, case)
        try:
            crm_record(facts, case)
        except sqlite3.Error as exc:
            log(f"CRM write failed for {facts['invoice_id']}: {exc}")
        drafted.append(facts)
        log(f"drafted {facts['invoice_id']} {facts['bucket']} {facts['amount_str']} in {time.time() - t0:.1f}s ({method})")
        if poster is not None:
            try:
                posted += 1 if poster.post_package(facts["invoice_id"], item, files) else 0
            except Exception as exc:
                log(f"channel post failed for {facts['invoice_id']}: {exc}")

    by_bucket = {}
    for bucket in BUCKET_ORDER:
        queued = [item for item in all_items if item_bucket(item) == bucket]
        done = [f for f in drafted if f["bucket"] == bucket]
        by_bucket[bucket] = {
            "queued": len(queued),
            "queued_dollars": round(sum(item_amount(i) for i in queued), 2),
            "drafted": len(done),
            "drafted_dollars": round(sum(f["amount"] for f in done), 2),
        }
    record = {
        "run_at": run_at,
        "model": model_label,
        "snapshot_id": meta["snapshot_id"],
        "as_of": meta["as_of"].isoformat(),
        "queue": display_path(queue_path),
        "drafted": len(drafted),
        "drafted_dollars": round(sum(f["amount"] for f in drafted), 2),
        "errors": errors,
        "skipped_already_drafted": skipped,
        "redraft_after_days": stale_days,
        "model_calls": calls,
        "posted": posted,
        "by_bucket": by_bucket,
        "duration_s": round(time.time() - started, 2),
    }
    record.update(review_counts())
    if stopped:
        record["stopped"] = stopped
    if isinstance(queue.get("totals"), dict):
        record["queue_totals"] = queue["totals"]
    append_summary(record)
    counts = ", ".join(f"{by_bucket[b]['drafted']} {b}" for b in BUCKET_ORDER)
    log(f"run done: drafted {len(drafted)} ({counts}), {skipped} already drafted, {errors} errors, "
        f"{record['duration_s']}s, model {model_label}")
    return 1 if errors and not drafted else 0


if __name__ == "__main__":
    sys.exit(main())
