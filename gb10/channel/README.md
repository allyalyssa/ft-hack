# channel

`dashboard.py` is the local approval surface and the demo fallback. Stdlib only, no network needed.

```
python3 channel/dashboard.py --host 0.0.0.0 --port 8090   # on the GB10, open http://<gb10-ip>:8090
python3 channel/dashboard.py --port 8089 --queue agent/fixtures/queue.sample.json
```

Pages: `/` queue by bucket with review state, `/invoice/<id>` the package with Approve, Edit and Reject, `/cases` mock Salesforce cases, `/summary` agent runs. Add `?refresh=30` to `/` to reload it every 30 seconds. Approve, Reject and Save write `out/actions/<id>/status.json` and the latest case row in `agent/crm_mock/crm.sqlite`. Default bind is 127.0.0.1.

JSON for the OpenClaw skill: `GET /api/summary`, `GET /api/queue?bucket=&state=&limit=`, `GET /api/invoice/<id>`, and `POST /api/invoice/<id>/approve|reject|edit` with a JSON body such as `{"by": "Mohammed", "reason": "..."}`. Same writes as the buttons. Tests: `python3 -m unittest channel/test_dashboard.py`.

`post.py` sends each drafted package and the heartbeat to Slack (incoming webhook) or Telegram (bot API). Set it up:

```
cp channel/channel.env.example channel/channel.env   # set CHANNEL, the webhook or bot token and chat id
python3 channel/post.py "test from the GB10"
```

With `CHANNEL=none` or no `channel.env` it does nothing. A failed post logs one line and returns False; drafting and the dashboard carry on. Packages end with "Reply approve INV-... or reject INV-...", which the OpenClaw collections skill handles. Set `DASHBOARD_URL` to add a review link.
