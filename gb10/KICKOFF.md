# Kickoff, Small-Dollar Collections Agent on the GB10

Dell x NVIDIA hackathon, 2026-10-25. Submissions close 18:00, top 8 demo at 18:30.
Repo: https://github.com/allyalyssa/ft-hack (empty right now, scaffold lands within the hour).

## Setup on your laptop, 5 minutes

1. Make a folder `ft-hack`. Put PLAN.md, CONTRACT.md and KICKOFF.md in it, and the four role files in `ft-hack/roles/`. When the repo scaffold lands, `git pull` on top of it.
2. Python 3.10 or newer. Nothing else to install, every script is standard library.
3. Roles B and C: install Ollama (https://ollama.com/download) and run `ollama pull qwen3:8b`. That gives you a local OpenAI-compatible endpoint at http://127.0.0.1:11434/v1 to build against. The GB10 will run a bigger model behind the same URL, so nothing changes when we move over.
4. Read CONTRACT.md before you write anything. It is the file layout and JSON shapes every role writes and reads. If you need to change it, say so in Discord first.

## Paste into your coding agent (Claude Code, Cursor, Codex), one per role

Open the agent inside your `ft-hack` folder and paste the block for your role.

### Role B, data and triage

```
Read PLAN.md, CONTRACT.md and roles/B-data-triage.md in this folder. Build what roles/B-data-triage.md specifies, to the schemas in CONTRACT.md. If data/ or triage/ already exist, extend them, do not rewrite them. Only touch data/ and triage/. Python 3.10+, standard library only, parse policy.yaml with a small hand-rolled parser for flat key: value lines. Seed the generator so numbers are stable. triage.py must run in under 2 seconds on 20,000 rows and print the elapsed time. Tests with unittest, no pytest. When done: run python3 data/generate.py, python3 data/generate.py --delta, python3 triage/triage.py, python3 -m unittest triage/test_triage.py, and show me the summary line, out/headline.json, and the test results.
```

### Role C, agent actions and approval

```
Read PLAN.md, CONTRACT.md and roles/C-agent-approval.md in this folder. Build what roles/C-agent-approval.md specifies, to the schemas in CONTRACT.md. If agent/ or channel/ already exist, extend them, do not rewrite them. Only touch agent/ and channel/. Python 3.10+, standard library only: urllib for the model call, sqlite3 for the mock Salesforce cases, http.server for the dashboard. No Flask, no OpenAI SDK, no requests. Read MODEL_BASE_URL and MODEL_NAME from platform/model.env, default to http://127.0.0.1:11434/v1 and qwen3:8b when the file is missing. The dashboard must work with zero network. channel/post.py may fail without breaking anything. Build against a fixture agent/fixtures/queue.sample.json with 6 items until out/queue.json exists. When done: run python3 agent/actions.py --dry-run --queue agent/fixtures/queue.sample.json --limit 6, then a real run with --limit 1 against Ollama, start the dashboard, approve one invoice through it, and show me the drafts and the before and after of status.json.
```

### Role D, video and deck

```
Read PLAN.md and roles/D-video-deck.md in this folder. Draft the six slides as a markdown outline with a headline and three to five flat lines per slide, and a 90-second voiceover script for the demo video following the storyline in PLAN.md. Leave a placeholder like {{queued}} wherever a number belongs; the numbers come from out/headline.json and out/summary.json once role B runs. Plain sentences. No em dashes, no questions as headlines, no "not X, it's Y" lines.
```

### Role A, platform (Mohammed)

Runs on the GB10 itself. See roles/A-platform.md and, once it lands, platform/RUNBOOK.md.

## Rules for everyone

- No remote LLM calls anywhere in product code. The model is the local endpoint in platform/model.env.
- Write to the paths in CONTRACT.md and nowhere else. B writes out/queue.json, C reads it. A moves files and schedules runs.
- Commit small and often to your own folders. Say in Discord before touching a shared file.
- 14:30 is the first end-to-end run on the box. 16:00 feature freeze. 17:15 rehearsal. 18:00 submit.
