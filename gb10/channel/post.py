#!/usr/bin/env python3
"""Post collections packages and short updates to Slack or Telegram. Stdlib only.

Config is channel/channel.env (copy channel.env.example):
    CHANNEL=slack|telegram|none
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=-1001234567890
    DASHBOARD_URL=http://192.168.2.2:8090     optional, adds a review link to each package

post_package(invoice_id, item, artifacts) and post_text(text) return True when the message
went out. With CHANNEL=none or no channel.env they do nothing and return False. Any failure
logs one line to stderr and returns False, so nothing upstream breaks when the network is down.

    python3 channel/post.py "test message from the GB10"
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path(os.environ.get("CHANNEL_ENV_FILE") or Path(__file__).resolve().with_name("channel.env"))
TIMEOUT_S = 10
SLACK_SECTION_MAX = 2900  # Slack caps a section's text at 3000 characters
BUCKET_LABEL = {"agent": "Agent works it", "writeoff": "Write off", "escalate": "Escalate to a human"}
FOOTER = "Nothing reaches the customer until a collector approves."


def _log(message):
    print(f"post.py: {message}", file=sys.stderr, flush=True)


def load_config():
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    cfg = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        cfg[key] = value.strip().strip('"').strip("'")
    channel = cfg.get("CHANNEL", "none").strip().lower()
    if channel not in ("slack", "telegram"):
        return None
    cfg["CHANNEL"] = channel
    return cfg


def _clip(text, limit):
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _secret_free(text, cfg):
    for key in ("TELEGRAM_BOT_TOKEN", "SLACK_WEBHOOK_URL"):
        if cfg.get(key):
            text = text.replace(cfg[key], "***")
    return text


def _send(url, payload, cfg, what, kind, quiet=False):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read().decode("utf-8", "replace")
        if kind == "telegram" and json.loads(body).get("ok") is not True:
            raise ValueError(body[:160])
        return True
    except urllib.error.HTTPError as exc:
        reason = f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:160]}"
        exc.close()
    except Exception as exc:  # network down, DNS, timeout, bad JSON: log and move on
        reason = f"{type(exc).__name__}: {exc}"
    if not quiet:
        _log(_secret_free(f"{kind} post failed for {what}: {' '.join(reason.split())}", cfg))
    return False


def _slack(cfg, payload, what, quiet=False):
    url = cfg.get("SLACK_WEBHOOK_URL")
    if not url:
        _log(f"slack post skipped for {what}: SLACK_WEBHOOK_URL is empty")
        return False
    return _send(url, payload, cfg, what, "slack", quiet)


def _telegram(cfg, text, what):
    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        _log(f"telegram post skipped for {what}: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty")
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    return _send(f"https://api.telegram.org/bot{token}/sendMessage", payload, cfg, what, "telegram")


def _slack_escape(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _review_url(cfg, invoice_id):
    base = (cfg.get("DASHBOARD_URL") or "").rstrip("/")
    return f"{base}/invoice/{invoice_id}" if base.startswith(("http://", "https://")) else ""


def _package(invoice_id, item, artifacts):
    item = item if isinstance(item, dict) else {}
    inv = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    arts = artifacts or {}
    subject, _, body = str(arts.get("customer_email.md") or "").partition("\n")
    try:
        case = json.loads(arts.get("salesforce_case.json") or "{}")
    except ValueError:
        case = {}
    try:
        amount = f"${float(inv.get('amount')):,.2f}"
    except (TypeError, ValueError):
        amount = str(inv.get("amount") or "")
    days = next((m.group(1) for r in item.get("reasons") or [] for m in [re.match(r"past_due_(\d+)", str(r))] if m), "")
    owner = str(inv.get("account_owner") or "")
    context = [BUCKET_LABEL.get(item.get("bucket"), str(item.get("bucket") or ""))]
    if days:
        context.append(f"{days} days past due")
    if owner:
        context.append(f"owner {owner}")
    if case.get("model"):
        context.append(f"drafted by {case['model']}")
    case_lines = [str(case.get("summary") or "")]
    if case.get("next_action"):
        case_lines.append(f"Next: {case['next_action']} ({case.get('next_action_date', '')})")
    return {
        "invoice_id": invoice_id,
        "title": " · ".join(x for x in (invoice_id, str(inv.get("account_name") or ""), amount) if x),
        "context": " · ".join(x for x in context if x),
        "subject": subject.strip(),
        "body": body.strip(),
        "slack": str(arts.get("customer_slack.md") or "").strip(),
        "note_label": f"Internal note to {owner}" if owner else "Internal note",
        "note": str(arts.get("internal_note.md") or "").strip(),
        "case": "\n".join(x for x in case_lines if x),
        "memo": str(arts.get("writeoff_memo.md") or "").strip(),
    }


def _slack_section(label, text, code=False):
    room = SLACK_SECTION_MAX - len(label) - 12
    text = _slack_escape(_clip(text, room))
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}*\n" + (f"```{text}```" if code else text)}}


def _slack_payload(p, cfg):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": _clip(p["title"], 150)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _slack_escape(p["context"]) or " "}]},
        _slack_section("Customer email", f"{p['subject']}\n\n{p['body']}"),
        _slack_section("Customer Slack message", p["slack"]),
        _slack_section(p["note_label"], p["note"]),
        _slack_section("Salesforce case", p["case"]),
    ]
    if p["memo"]:
        blocks.append(_slack_section("Write-off memo", p["memo"], code=True))
    link = _review_url(cfg, p["invoice_id"])
    if link:
        blocks.append({"type": "actions", "elements": [{"type": "button", "style": "primary", "url": link,
                                                         "text": {"type": "plain_text", "text": "Review in dashboard"}}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   f"Reply `approve {p['invoice_id']}` or `reject {p['invoice_id']}`. {FOOTER}"}]})
    return {"text": f"Collections package for approval: {p['title']}", "blocks": blocks}


def _plain_text(p, cfg):
    parts = [p["title"], p["context"], f"Customer email\n{p['subject']}\n\n{p['body']}",
             f"Customer Slack message\n{p['slack']}", f"{p['note_label']}\n{p['note']}", f"Salesforce case\n{p['case']}"]
    if p["memo"]:
        parts.append(f"Write-off memo\n{p['memo']}")
    link = _review_url(cfg, p["invoice_id"])
    parts.append(f"Reply \"approve {p['invoice_id']}\" or \"reject {p['invoice_id']}\". {FOOTER}" + (f"\nReview: {link}" if link else ""))
    return "\n\n".join(x for x in parts if x)


def _telegram_text(p, cfg):
    def part(label, text, limit, pre=False):
        body = html.escape(_clip(text, limit))
        return f"<b>{html.escape(label)}</b>\n" + (f"<pre>{body}</pre>" if pre else body)

    parts = [f"<b>{html.escape(p['title'])}</b>\n{html.escape(p['context'])}",
             part("Customer email", f"{p['subject']}\n\n{p['body']}", 900),
             part("Customer Slack message", p["slack"], 600),
             part(p["note_label"], p["note"], 700),
             part("Salesforce case", p["case"], 400)]
    if p["memo"]:
        parts.append(part("Write-off memo", p["memo"], 900, pre=True))
    footer = f"Reply \"approve {p['invoice_id']}\" or \"reject {p['invoice_id']}\". {FOOTER}"
    link = _review_url(cfg, p["invoice_id"])
    if link:
        footer += f"\nReview: {link}"
    parts.append(html.escape(footer))
    return "\n\n".join(parts)


def post_text(text):
    cfg = load_config()
    text = str(text or "").strip()
    if cfg is None or not text:
        return False
    if cfg["CHANNEL"] == "slack":
        return _slack(cfg, {"text": _slack_escape(_clip(text, 3900))}, "text")
    return _telegram(cfg, html.escape(_clip(text, 4000)), "text")


def post_package(invoice_id, item, artifacts):
    """artifacts maps file name (customer_email.md, salesforce_case.json, ...) to file text."""
    cfg = load_config()
    if cfg is None:
        return False
    p = _package(invoice_id, item, artifacts)
    if cfg["CHANNEL"] == "slack":
        if _slack(cfg, _slack_payload(p, cfg), invoice_id, quiet=True):
            return True
        return _slack(cfg, {"text": _slack_escape(_clip(_plain_text(p, cfg), 3900))}, invoice_id)
    return _telegram(cfg, _telegram_text(p, cfg), invoice_id)


if __name__ == "__main__":
    message = " ".join(sys.argv[1:]).strip()
    if not message:
        print('usage: python3 channel/post.py "message"', file=sys.stderr)
        sys.exit(2)
    if load_config() is None:
        print(f"no channel set up ({ENV_PATH} missing or CHANNEL=none), nothing posted")
        sys.exit(0)
    sys.exit(0 if post_text(message) else 1)
