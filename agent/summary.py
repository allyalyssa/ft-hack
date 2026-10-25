"""Refresh out/summary.json and post the heartbeat line to the channel.

python agent/summary.py [--no-post]
"""
import argparse
import sys

import store

sys.path.insert(0, str(store.ROOT / "channel"))
import post  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-post", action="store_true")
    args = ap.parse_args()
    queue = store.read_json(store.QUEUE)
    line = store.heartbeat(store.build_summary(queue))
    print(line)
    if not args.no_post:
        post.send(line)


if __name__ == "__main__":
    main()
