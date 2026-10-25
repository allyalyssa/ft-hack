# Cron and heartbeat (role A)

Three things have to run, per `CONTRACT.md`:
1. Triage + act, every 2 minutes, for the demo.
2. Heartbeat / summary, set to every 10 minutes for the demo (CONTRACT.md
   says daily in production).
3. One full, unlimited run, once, before either schedule starts, so the
   dashboard has a real backlog to show.

Adjust `$HOME/ft-hack` below if the repo ends up cloned or mounted somewhere
else inside the sandbox (same caveat as `platform/sync.sh`'s `REMOTE_DIR`).

## OpenClaw cron, partially verified

Confirmed by fetching `docs.openclaw.ai/cli` today (2026-10-25): the cron
subcommands are

```
openclaw cron status|list|get|show|add|edit|rm|enable|disable|runs|run|scratch
```

`cron` is aliased to `automations`, so `openclaw automations ...` works the
same way. The heartbeat family is

```
openclaw system heartbeat last|enable|disable
```

Confirmed by fetching `docs.openclaw.ai/automation/cron-jobs` today: the
only complete worked example on that page is a one-shot reminder, not a
recurring job, quoted exactly as found:

```
openclaw automations create "2027-02-01T16:00:00Z" \
  --name "Reminder" \
  --session main \
  --system-event "Reminder: check the automations docs draft" \
  --wake now \
  --delete-after-run
```

That page confirms recurring intervals and cron expressions exist as a
scheduler feature, alongside one-shot reminders and inbound webhooks, but
it does not show the flag that carries a repeating schedule on this
command, and it uses `create` where the CLI page above lists `add`. That
inconsistency was not resolved by anything else fetched today.

**Unverified best-effort draft below, confirm with `--help` on the box
before the demo depends on it:**

```
openclaw automations add \
  --name "ft-hack-triage" \
  --session main \
  --schedule "*/2 * * * *" \
  --system-event "cd $HOME/ft-hack && python3 triage/triage.py && python3 agent/actions.py --limit 5"

openclaw automations add \
  --name "ft-hack-heartbeat" \
  --session main \
  --schedule "*/10 * * * *" \
  --system-event "cd $HOME/ft-hack && python3 agent/summary.py"
```

`--schedule` is a guess at the flag name for a cron expression; it is not
shown verbatim on either fetched page. Run `openclaw automations add
--help` (or `openclaw cron add --help`) first and fix this block if the
real flag differs.

## Plain crontab fallback, verified, works without OpenClaw

Standard `cron(5)` syntax, nothing NVIDIA- or OpenClaw-specific to confirm.

```
crontab -e
```

```
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Triage + act, every 2 minutes, for the demo. The second actions line drafts one
# write-off per cycle so the tiny invoices show up alongside the outreach drafts.
*/2 * * * * cd $HOME/ft-hack && python3 triage/triage.py && python3 agent/actions.py --limit 5 && python3 agent/actions.py --bucket writeoff --limit 1 >> /tmp/ft-hack-cron.log 2>&1

# Heartbeat / summary, every 10 minutes for the demo (CONTRACT.md: daily in production)
*/10 * * * * cd $HOME/ft-hack && python3 agent/summary.py >> /tmp/ft-hack-heartbeat.log 2>&1
```

Watch it fire:

```
tail -f /tmp/ft-hack-cron.log /tmp/ft-hack-heartbeat.log
```

## One-time full run, not scheduled, by hand

Before either schedule starts, seed a real backlog so the dashboard has
something to show:

```
cd $HOME/ft-hack && python3 triage/triage.py && python3 agent/actions.py
```

Do not run it unlimited against the model: 2,589 drafts at 15 to 30 s each is many hours. Seed with a real batch instead, `python3 agent/actions.py --limit 40`, so the dashboard shows real drafts for the top of each bucket and "not drafted yet" for the rest. Never seed with `--dry-run` on the demo box, a judge who clicks a random invoice would see placeholder text.

## Which one is verified

Trust the plain crontab block today. It needs nothing beyond standard
cron and both schedule lines are exact. The OpenClaw block uses confirmed
subcommand names and a confirmed one-shot example shape, but the repeating
schedule flag is a guess, not a quote, run it only after checking `--help`
on the box.

## Sources

- `docs.openclaw.ai/cli`, fetched 2026-10-25: cron subcommand list, the
  `automations` alias, the `system heartbeat` commands.
- `docs.openclaw.ai/automation/cron-jobs`, fetched 2026-10-25: the one-shot
  `automations create` example, and confirmation that recurring intervals
  and cron expressions are a supported scheduler feature, without the flag.
- `docs.openclaw.ai/automation`, fetched 2026-10-25: same recurring-schedule
  feature mentioned at a higher level, same gap.
