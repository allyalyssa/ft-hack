# Role A, Platform (Mohammed)

Goal: the GB10 runs the loop on its own, with the model local and egress locked. Everything below is verified against NVIDIA's NemoClaw docs and DGX Spark playbooks as of today; exact prompts in the installer may differ.

## 1. Get the box online through your Mac (no Wi-Fi on the GB10)
The Dell Pro Max GB10 has a 10GbE RJ-45 and four USB-C. Non-wireless SKUs exist and show a Wi-Fi option in BIOS that does nothing (Dell KB 000467537).
- Mac: System Settings, General, Sharing, Internet Sharing. Share your connection from Wi-Fi to the USB-C ethernet adapter (or Thunderbolt Bridge). Turn it on.
- Cable Mac adapter to GB10 RJ-45. The GB10 gets a 192.168.2.x address from the Mac. Find it: `arp -a | grep 192.168.2` on the Mac, or read it off the GB10 screen with `ip a`.
- `ssh <user>@192.168.2.X`. Keep this link up all day. It is also the sync path for data drops.
- If the venue has a wired drop, use that instead and skip sharing.

## 2. Inventory, first 5 minutes
```bash
nvidia-smi
docker ps
ollama list          # anything preloaded saves hours
df -h /
```
Nothing is preloaded unless `ollama list` says otherwise. The model download is the critical path, so step 3 starts before anything else.

## 3. Model, start the pull before anything else
```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
ollama pull qwen3:8b                                       # 5 GB. B and C integrate on the box against this within minutes
nohup ollama pull gpt-oss:20b > /tmp/pull-20b.log 2>&1 &   # 14 GB, tool calling, fast on the GB10. This is the demo model.
# Only if the 20b lands with hours to spare and the link is doing well over 100 Mbps:
# nohup ollama pull gpt-oss:120b > /tmp/pull-120b.log 2>&1 &   # 65 GB
```
Sizes and tool support are from ollama.com/library/gpt-oss. Nemotron 3 Super 120B is NVIDIA's default in the NemoClaw blog, 87 GB and 30 to 90 s a response. Skip it for a live loop.

Bandwidth math: 14 GB takes about 40 minutes at 50 Mbps and about 10 minutes at 200 Mbps. 65 GB takes 3 hours at 50 Mbps. Test the link first with a 1 GB pull and `watch -n5 'tail -c 300 /tmp/pull-20b.log'`. If venue Wi-Fi is slow, a phone hotspot on the Mac is often faster than shared venue Wi-Fi, and the Mac can share either one down the cable. A second option: pull on a teammate's laptop with a better link and `rsync ~/.ollama/models/` to the box over the cable, Ollama picks the blobs up after a restart.

Write `platform/model.env`:
```
MODEL_BASE_URL=http://127.0.0.1:11434/v1
MODEL_NAME=qwen3:8b
```
Swap `MODEL_NAME` once the big pull lands and re-run the smoke test.

## 4. NemoClaw and OpenShell
```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash      # Express Install on Spark: Node, OpenShell, NemoClaw CLI, sandbox
```
If OpenShell is not pulled in: `curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh`.
Pick Ollama as the inference provider and point it at the host's Ollama. Name the sandbox `collections`.
```bash
openshell term            # into the sandbox
openclaw tui              # sanity check the agent answers from the local model
nemoclaw collections policy-add    # approve egress for the channel API only (slack.com or api.telegram.org)
```
Mount or clone the repo into the sandbox workspace so `triage/` and `agent/` are visible to the agent.

## 5. Channel
Telegram is the path NVIDIA documents in onboarding: create a bot with @BotFather, paste the token into the wizard, the token stays in the OpenShell gateway. Slack via OpenClaw's Slack integration if the team prefers. Either way role C's `channel/post.py` must work with the local dashboard alone if egress fails.

## 6. Schedule and sync
- OpenClaw cron, every 2 min: `cd /workspace && python3 triage/triage.py && python3 agent/actions.py --limit 5`
- OpenClaw heartbeat, daily (set to 10 min for the demo): `python3 agent/summary.py`
- Laptop to box sync, on the Mac, every 2 min via launchd or a `while true; do rsync -az data/exports/ user@192.168.2.X:/workspace/data/inbox/; sleep 120; done` in a terminal. `platform/sync.sh` has it.

## 7. Prove always-on
Before 16:00: `sudo reboot`. Confirm Ollama, the OpenShell sandbox and the cron come back without a hand on the keyboard. Then pull the Mac uplink for one cycle and confirm the local dashboard still updates. Both go in the demo.

## Verification checklist
- [ ] `curl http://127.0.0.1:11434/v1/models` from inside the sandbox lists the model
- [ ] cron fired twice on its own (check `out/summary.json` timestamps)
- [ ] egress policy shows one allowed host
- [ ] survives reboot
- [ ] `MODEL_NAME` is gpt-oss:20b (or bigger) and a draft takes under 15 s
