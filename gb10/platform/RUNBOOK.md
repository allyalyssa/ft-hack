# Platform runbook (role A)

Ordered checklist for getting the GB10 to run the loop on its own, egress
locked, and proving it survives a reboot and a pulled cable. Expands
roles/A-platform.md with exact commands. Troubleshooting table at the end.

## 0. Before you start
- Mac and GB10 both powered on, within reach of each other.
- USB-C to RJ-45 ethernet adapter (or Thunderbolt Bridge) for the Mac.
- Nothing is preloaded on the GB10 this time, no USB with models either.
  The model download is the critical path: start it first.

## 1. Get the box online through your Mac
1. On the Mac: System Settings -> General -> Sharing -> Internet Sharing.
   Share connection **From**: Wi-Fi. **To computers using**: the USB-C
   ethernet adapter (or Thunderbolt Bridge). Toggle Internet Sharing on.
2. Cable the Mac's adapter to the GB10's RJ-45 port.
3. Find the GB10's IP from the Mac:
   ```
   arp -a | grep 192.168.2
   ```
   Or read it directly off the GB10: `ip a`.
4. `ssh <user>@192.168.2.X` and keep that terminal open all day. It is also
   the path platform/sync.sh uses.
5. If the venue has a wired drop, use that instead and skip Internet Sharing.

## 2. Inventory (first 5 minutes)
```
bash platform/setup_gb10.sh
```
Prints `nvidia-smi`, `docker ps`, `ollama list`, `df -h /`, `free -g` before
touching anything. Expect `ollama list` empty, nothing is preloaded.

## 3. Model pull (the critical path)
The same command continues into installing Ollama, binding it to
`0.0.0.0`, pulling `qwen3:8b` in the foreground, then starting the
background pull of `gpt-oss:20b` (14 GB, tool calling). `gpt-oss:120b`
(65 GB) is opt-in only, too slow for a live loop unless the link is fast
and the schedule has room:
```
BIG_MODEL=gpt-oss:120b bash platform/setup_gb10.sh
```
Re-running `platform/setup_gb10.sh` is safe, it skips finished steps.

**Bandwidth note.** 14 GB is about 40 minutes at 50 Mbps, 10 minutes at
200 Mbps. The `qwen3:8b` pull (foreground, runs first) is your bandwidth
test, time it and do the arithmetic before deciding whether 120b is in
reach today. A phone hotspot shared from the Mac is often faster than
venue Wi-Fi, try both and keep the faster one. If a teammate's laptop has
a better link, have them pull the model there and `rsync` their
`~/.ollama/models/` to the box over the cable, then on the box:
`sudo systemctl restart ollama` so it picks up the new blobs.

Check the big pull's progress any time:
```
tail -f /tmp/pull-big.log
```

## 4. NemoClaw and OpenShell
`platform/setup_gb10.sh` installs NemoClaw if it is not already on PATH,
then prints the interactive steps it cannot answer for you:
- Express Install asks to confirm Node, OpenShell, the NemoClaw CLI/sandbox.
- Pick **Ollama** as the inference provider, point it at `127.0.0.1:11434`.
- Name the sandbox `collections`.

Then by hand:
```
openshell term
openclaw tui                        # confirm the agent answers from the local model
nemoclaw collections policy-add     # see platform/openshell_policy.md
```
Mount or clone this repo into the sandbox workspace so `triage/` and `agent/`
are visible inside it.

## 5. Channel
Telegram (via @BotFather) or Slack via OpenClaw's integration. Either way,
`channel/post.py` (role C) must still work against the local dashboard
alone if egress is down.

## 6. Schedule and sync
- Cron entries and heartbeat: `platform/cron.md`.
- On the Mac, pick one:
  ```
  bash platform/sync.sh              # loops every 120s until Ctrl-C
  ```
  or load `platform/com.ft-hack.sync.plist` (see the comment block in that
  file for `launchctl load`/`unload`). Don't run both at once.
- Live demo drop: `bash platform/drop_delta.sh`.

## 7. Prove always-on
1. Before 16:00: `sudo reboot` on the GB10.
2. After it comes back with no hand on the keyboard, confirm:
   ```
   systemctl is-active ollama
   openshell term            # sandbox still there
   ```
   and that `out/summary.json` has a new timestamp on its own (cron fired
   without you).
3. Pull the Mac's uplink cable for one cron cycle (2 minutes). Confirm the
   local dashboard (`http://<gb10-ip>:8090`) still updates. Both go in the demo.

## Verification checklist
- [ ] `curl http://127.0.0.1:11434/v1/models` from inside the sandbox lists the model
- [ ] `bash platform/smoke_model.sh` passes, under 20s
- [ ] cron fired twice on its own (`out/summary.json` timestamps, 2 min apart)
- [ ] `nemoclaw collections policy-add` shows one allowed host
- [ ] survives reboot
- [ ] `MODEL_NAME` is the big model and a draft takes under 15s (`platform/swap_model.sh <name>`)

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Ollama unreachable from the sandbox | Service bound to localhost only, or sandbox on a different network namespace | Confirm the override: `cat /etc/systemd/system/ollama.service.d/override.conf`. Re-run `platform/setup_gb10.sh`. From inside the sandbox: `curl http://127.0.0.1:11434/v1/models` (or the host's real IP if the sandbox is not on the host network) |
| Model pull stalls | Venue Wi-Fi shared through the Mac is too slow | Check `tail -f /tmp/pull-big.log`. Try a phone hotspot instead. Keep building on `qwen3:8b`, swap later with `platform/swap_model.sh <name>` once the pull lands |
| Cron not firing | OpenClaw cron not loaded, or the plain crontab fallback never installed | `crontab -l` to check. Re-apply the block in `platform/cron.md`. Confirm `out/summary.json`'s mtime is moving |
| Dashboard port busy | Something else on 8090 | `sudo lsof -i :8090`, kill the stale process or change the dashboard's port (role C) |
| Egress blocked (Slack/Telegram) | Policy too tight, or DNS/routing inside the sandbox | `nemoclaw collections policy-add` again with the exact host (see `platform/openshell_policy.md`). The demo must still work with the local dashboard alone |

## Quick commands
```
bash platform/setup_gb10.sh                 # idempotent, re-run any time
bash platform/swap_model.sh gpt-oss:20b     # swap model + smoke test
bash platform/smoke_model.sh                # smoke test only
bash platform/sync_once.sh                  # one push from the Mac
bash platform/drop_delta.sh                 # the live demo drop
```
