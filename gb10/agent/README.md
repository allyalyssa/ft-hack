# agent

Drafts a package per queued invoice with the local model: customer email, customer Slack message, internal note, Salesforce case, and a write-off memo for the writeoff bucket. Writes `out/actions/<invoice_id>/`, logs the case in `agent/crm_mock/crm.sqlite`, appends a run to `out/summary.json`, and posts the package through `channel/post.py` when a channel is set up.

```
python3 agent/actions.py --limit 5                    # the cron line
python3 agent/actions.py --bucket writeoff --limit 2  # one bucket
python3 agent/actions.py --invoice INV-0042           # one invoice
python3 agent/actions.py --dry-run --limit 6 --queue agent/fixtures/queue.sample.json
python3 agent/review.py list --state drafted
python3 agent/review.py show INV-0042
python3 agent/review.py approve INV-0042 --by "Mohammed"
python3 agent/summary.py                              # heartbeat line, posts it if a channel is set up
python3 -m unittest agent/test_actions.py channel/test_dashboard.py
```

Model: `MODEL_BASE_URL` and `MODEL_NAME` from `platform/model.env`, else `http://127.0.0.1:11434/v1` and `qwen3:8b`. One call returns every draft as JSON; anything missing or unusable gets one call of its own.

Order: never-drafted invoices first, then the rest, each by amount descending. Skipped: a package drafted, edited or approved less than `stale_touch_days` (8) ago, or one with the same `action_id`. Always redrafted: a rejected package, a bucket change, a dry-run package on a real run.

Seed the demo box with a real batch (`--limit 40`, about 20 to 40 s an invoice on qwen3:8b); a full run is hours. `--dry-run` writes templated text for testing without a model, and a later real run replaces it.

Prompts: `agent/prompts/`. OpenClaw skill: copy `agent/skills/collections/` into the OpenClaw skills folder. It runs in the sandbox and only calls the dashboard's JSON API with curl, so set `DASHBOARD_URL` there (see the skill). If drafts ignore the prompt rules on Ollama, raise its context: `OLLAMA_CONTEXT_LENGTH=8192`.
