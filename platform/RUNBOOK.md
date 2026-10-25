# Runbook: the GB10 with no internet

Nothing in this repo needs the internet at runtime. The model runs in Ollama on the box, every script is Python
standard library, and the dashboard loads no outside files. The only code that can reach outside is
`channel/post.py`, and only when `platform/channel.env` is filled in.

## 0. Bring these to the box before it goes offline

The box cannot download anything, so these have to come from a machine that can (the Mac), over the cable or USB:

| What | How |
|---|---|
| This repo | `rsync -az --exclude .git --exclude out ./ user@192.168.2.X:ft-hack/` from the repo on the Mac |
| Ollama itself, if `ollama --version` fails on the box | download `ollama-linux-arm64.tgz` from ollama.com on the Mac, copy it over, `sudo tar -C /usr -xzf ollama-linux-arm64.tgz` |
| The models | `ollama pull qwen3:8b` (and `gpt-oss:20b`) on the Mac, then copy `~/.ollama/models/` to the box (step 2) |
| NemoClaw / OpenShell / OpenClaw | their installers download from the internet. Install while the Mac shares its connection, or skip them: the loop below runs without them |

## 1. Cable

Mac System Settings, General, Sharing, Internet Sharing from Wi-Fi to the USB-C ethernet adapter only if something in
step 0 still needs downloading. Otherwise a plain cable is enough for ssh and rsync. Find the box with
`arp -a | grep 192.168.2` on the Mac, then `ssh user@192.168.2.X`.

## 2. Ollama on the box

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\nEnvironment="OLLAMA_CONTEXT_LENGTH=8192"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama

# models copied from the Mac (blobs are the same on every OS and CPU)
rsync -az ~/.ollama/models/ user@192.168.2.X:/tmp/ollama-models/          # on the Mac
sudo rsync -a /tmp/ollama-models/ /usr/share/ollama/.ollama/models/      # on the box
sudo chown -R ollama:ollama /usr/share/ollama/.ollama/models && sudo systemctl restart ollama
ollama list
```

`platform/model.env` picks the model. qwen3:8b is the default; for the demo model:

```
MODEL_BASE_URL=http://127.0.0.1:11434/v1
MODEL_NAME=gpt-oss:20b
```

## 3. Check everything

```bash
python3 platform/preflight.py
```

It runs all three test suites, checks the model is served locally, runs one full cycle with one real draft, opens
every dashboard page, and does all of it under `platform/offline_guard`, which refuses any connection that leaves
the machine. The last line has to say `0 failed`. A draft over 15 s is a warning, not a failure.

## 4. Run it

Foreground, for the demo laptop or a quick start:

```bash
python3 platform/run.py          # dashboard on :8080 plus a cycle every 2 minutes, Ctrl-C stops both
```

Always on, survives a reboot (use this or the OpenClaw cron in step 5, not both):

```bash
mkdir -p ~/.config/systemd/user && cp platform/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now collections-dashboard.service collections-cycle.timer
sudo loginctl enable-linger "$USER"      # start at boot without anyone logging in
journalctl --user -u collections-cycle -f
```

The unit files assume the repo is at `~/ft-hack`. Change `WorkingDirectory` if it is not.

First thing after starting: `python3 agent/actions.py --limit 20` so the dashboard opens with packages waiting.

## 5. OpenClaw (if NemoClaw is installed)

- Skill: copy `agent/skills/collections/` into the OpenClaw workspace `skills/` folder. Then "work the queue",
  "show me INV-116288" and "approve INV-116288" work in `openclaw tui`.
- Cron every 2 minutes: `cd ~/ft-hack && python3 platform/run.py --once`
- Heartbeat: `cd ~/ft-hack && python3 agent/summary.py`
- Inside the OpenShell sandbox the model is the host's Ollama, so `MODEL_BASE_URL` may need the host address the
  sandbox sees instead of 127.0.0.1. `curl $MODEL_BASE_URL/models` from inside the sandbox must list the model.
- If the dashboard runs inside the sandbox, forward port 8080 to the host.

## 6. Live drop for the demo

Upload page (the visible version): `python3 data/demo_batch.py` writes three 300-invoice batches to `data/demo/`.
On the dashboard open Upload, select every file in `data/demo/batch-1`, and upload. Triage, the stale-draft check and
disputes run in about ten seconds, and the report shows each filter's cut (batch-1: 300 received, 101 paid, 59 under
30 days, 33 touched recently, 24 over $500, 83 queued), the queue growth, and buttons to draft three of them with the
model and to ask about one. "Remove all uploads" on the same page puts the queue back for the next rehearsal.

Over rsync instead, on the Mac:

```bash
python3 data/generate.py --delta                       # 200 invoices two days later -> data/delta/
rsync -az data/delta/ user@192.168.2.X:ft-hack/data/inbox/
```

The next cycle reads the inbox, the queue grows from 5,332 to 5,688, and the dashboard header shows snapshot
2026-10-27. Empty the inbox afterwards: `rm ~/ft-hack/data/inbox/*` on the box (keep `.gitkeep`).
`platform/sync.sh user@192.168.2.X` repeats the copy every 2 minutes if you want it continuous.

## 7. Pull the cable

Unplug the Mac. The timer keeps firing, drafts keep landing, the dashboard on the box's own screen keeps working.
To show that nothing in the code reaches outside, run the loop under the guard:

```bash
PYTHONPATH=platform/offline_guard python3 platform/run.py
```

Any outside connection attempt would print `offline guard: blocked network access to ...`.

## 8. Reset between rehearsals

```bash
rm -rf out/actions out/summary.json agent/crm_mock/crm.sqlite && rm -f data/inbox/*.csv data/inbox/*.json
```

## Verification checklist

- [ ] `python3 platform/preflight.py` ends with `0 failed`
- [ ] `ollama list` shows the model; `MODEL_NAME` in `platform/model.env` matches it
- [ ] one draft takes under 15 s (preflight prints the time)
- [ ] two cycles fired on their own: `python3 -c "import json; print([r['started_at'] for r in json.load(open('out/summary.json'))['runs']][-2:])"`
- [ ] `sudo reboot`, then the dashboard answers on :8080 and a new run appears in `out/summary.json` without touching the keyboard
- [ ] cable pulled, the next cycle still drafts
