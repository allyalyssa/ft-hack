"""Post newly drafted packages to Slack or Telegram. Optional: any failure is logged and skipped.

python channel/post.py [--limit 10]

Configured from the environment or platform/channel.env:
    SLACK_WEBHOOK_URL                      Slack incoming webhook
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   Telegram bot
    DASHBOARD_URL                          link back to the dashboard, default http://localhost:8080
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import store  # noqa: E402

KEYS = ("SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DASHBOARD_URL")


def config():
    env = store.read_env_file(ROOT / "platform" / "channel.env")
    return {k: os.environ.get(k) or env.get(k, "") for k in KEYS}


def send(text):
    cfg = config()
    if cfg["SLACK_WEBHOOK_URL"]:
        url, body = cfg["SLACK_WEBHOOK_URL"], {"text": text}
    elif cfg["TELEGRAM_BOT_TOKEN"] and cfg["TELEGRAM_CHAT_ID"]:
        url = f"https://api.telegram.org/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage"
        body = {"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": True}
    else:
        print("channel: nothing configured, skipping (the dashboard still has everything)")
        return False
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"channel: post failed, skipping: {e}")
        return False


def package_text(invoice_id, action, dashboard_url):
    item, files = action["item"], action["files"]
    lines = [f"{invoice_id}  {item['account_name']}  {store.money(item['balance'], item.get('currency', 'USD'))}  "
             f"{item['days_past_due']} days past due  ({item['bucket']})"]
    if "customer_email.md" in files:
        lines += ["", "Customer email:", files["customer_email.md"].strip()]
    if "customer_slack.md" in files:
        lines += ["", "Customer Slack:", files["customer_slack.md"].strip()]
    if "writeoff_memo.md" in files:
        lines += ["", f"Write-off proposed. {action['case'].get('summary', '')}"]
    lines += ["", f"Approve: reply \"approve {invoice_id}\" to the agent, or open {dashboard_url}/invoice/{invoice_id}"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    dashboard_url = config()["DASHBOARD_URL"] or "http://localhost:8080"
    unposted = []
    for invoice_id, status in store.all_statuses().items():
        posted = store.read_json(store.action_dir(invoice_id) / "posted.json", {})
        if status["state"] == "drafted" and posted.get("action_id") != status["action_id"]:
            unposted.append((status["at"], invoice_id, status["action_id"]))
    sent = 0
    for _, invoice_id, aid in sorted(unposted)[:args.limit]:
        if not send(package_text(invoice_id, store.load_action(invoice_id), dashboard_url)):
            break
        store.write_json(store.action_dir(invoice_id) / "posted.json", {"action_id": aid, "at": store.now()})
        sent += 1
    print(f"channel: posted {sent} of {len(unposted)} new packages")


if __name__ == "__main__":
    main()
