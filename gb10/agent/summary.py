#!/usr/bin/env python3
"""Print one heartbeat line from out/summary.json and post it when channel/channel.env sets a channel.

    python3 agent/summary.py            # print and post
    python3 agent/summary.py --no-post  # print only
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import actions as A  # noqa: E402  (same folder, role C)


def short_time(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return str(value or "")


def heartbeat_line(runs, live):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not runs:
        return f"Collections heartbeat {now}: no agent runs yet."
    last = runs[-1]
    buckets = last.get("by_bucket") or {}
    queued = sum(b.get("queued", 0) for b in buckets.values())
    queued_dollars = sum(b.get("queued_dollars", 0.0) for b in buckets.values())
    split = ", ".join(f"{(buckets.get(b) or {}).get('queued', 0):,} {label}"
                      for b, label in (("agent", "agent"), ("writeoff", "write off"), ("escalate", "escalate")))
    parts = [
        f"queued {queued:,} ({split}), {A.money(queued_dollars)}",
        f"drafted {live['packages']:,} packages, {live['awaiting_review']:,} awaiting review",
        f"approved {live['approved']:,} ({A.money(live['approved_dollars'])})",
        f"written off {A.money(live['written_off_dollars'])}",
        f"rejected {live['rejected']:,}",
        f"model {last.get('model', '')}",
        f"last run {short_time(last.get('run_at'))} drafted {last.get('drafted', 0)} in {last.get('duration_s', 0)}s",
    ]
    if last.get("stopped"):
        parts.append(f"last run stopped: {last['stopped']}")
    return f"Collections heartbeat {now}: " + "; ".join(parts) + "."


def load_post():
    if not A.POST_PY.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("collections_channel_post", A.POST_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        print(f"channel/post.py not loaded: {exc}", file=sys.stderr)
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Print the collections heartbeat line and post it to the channel.")
    p.add_argument("--no-post", action="store_true", help="print only")
    p.add_argument("--summary", default=str(A.SUMMARY_PATH), help="summary file (default out/summary.json)")
    args = p.parse_args(argv)
    runs = A.read_json(args.summary)
    if isinstance(runs, dict):
        runs = runs.get("runs")
    line = heartbeat_line(runs if isinstance(runs, list) else [], A.review_counts())
    print(line)
    if not args.no_post:
        post = load_post()
        if post is not None and hasattr(post, "post_text"):
            post.post_text(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
