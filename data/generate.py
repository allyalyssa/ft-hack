"""Generate a fake but believable accounts-receivable world.

python data/generate.py           full export to data/exports/
python data/generate.py --delta   small follow-up export to data/delta/ (drop it into data/inbox/ live)

Everything is seeded, so the same command always writes the same numbers.
All names and email domains are invented; domains use the reserved .example TLD.
"""
import argparse
import csv
import itertools
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 10, 25)
SEED = 42
N_INVOICES = 20_000
N_ACCOUNTS = 1_200

OWNERS = [
    "Priya Raman", "Marcus Bell", "Elena Sato", "Jordan Okafor", "Hannah Lindqvist", "Diego Ferreira",
    "Amara Nwosu", "Tomas Kowalski", "Mei Chen", "Sam Whitaker", "Leila Haddad", "Noah Brennan",
]
COLLECTORS = [
    "Ana Ruiz", "Ben Carter", "Chloe Dubois", "Dev Patel", "Erin Walsh", "Felix Mueller", "Grace Kim", "Hugo Santos",
]

PREFIXES = [
    "Vector", "Tensor", "Lumen", "Quanta", "Neural", "Cortex", "Synapse", "Gradient", "Latent", "Prism",
    "Helix", "Orbit", "Cobalt", "Nimbus", "Atlas", "Ember", "Kestrel", "Meridian", "Onyx", "Pixel",
    "Radiant", "Sable", "Tidal", "Umbra", "Vertex", "Willow", "Zephyr", "Aurora", "Beacon", "Cinder",
    "Delta", "Echo", "Flux", "Granite", "Harbor", "Iris", "Juniper", "Kinetic", "Lattice", "Mosaic",
]
SUFFIXES = [
    "Labs", "AI", "Systems", "Robotics", "Analytics", "Dynamics", "Works", "Intelligence", "Compute", "Data",
    "Machines", "Cloud", "Logic", "Vision", "Signal", "Forge", "Foundry", "Networks", "Studio", "Health",
    "Bio", "Bank", "Mobility", "Energy", "Security", "Media", "Retail", "Freight", "Learning", "Research",
    "Ops", "Grid", "Ventures", "Metrics", "Agents",
]

NOTE_TEMPLATES = [
    "Left voicemail for AP, will retry Thursday",
    "Sent statement to billing contact, no reply yet",
    "AP says payment run is on the 15th",
    "Customer asked for a copy of the invoice, resent",
    "PO number missing, asked {owner} to chase the buyer",
    "Contact bounced, need new AP email from {owner}",
    "Customer says this was paid, asked for remittance",
    "Spoke with AP, payment scheduled for next run",
    "Customer disputes usage overage, waiting on deal desk",
    "Promised to pay end of month",
    "Vendor portal upload required, submitted",
    "Customer wants it consolidated with next invoice",
]

COLLECTOR_SLACK = [
    "Any update from {account} on {invoice_id}? ${balance} still open, {days} days past due.",
    "{account} keeps ignoring statements on {invoice_id}. Can you nudge your champion?",
    "Billing contact at {account} bounced. Do you have a new AP contact?",
    "Customer says {invoice_id} was paid but I can't find the remittance.",
]
OWNER_SLACK = [
    "Their champion left last month, I'll find the new AP contact.",
    "They dispute the overage charge, looping in deal desk.",
    "On a call with them Friday, will raise it.",
    "Honestly this is tiny, can we just write it off?",
    "They're switching procurement systems, expect a delay.",
    "Pinged their CFO, should clear this week.",
]
CHANNELS = ["#collections", "#ar-escalations", "#deal-desk"]

SF_FIELDS = [
    "invoice_id", "account_id", "account_name", "account_owner", "owner_email", "billing_contact_email",
    "currency", "amount", "amount_paid", "status", "issue_date", "due_date", "last_touch_date", "dispute_flag",
]
SHEET_FIELDS = ["invoice_id", "collector", "touch_date", "note"]


def slug(name):
    return name.lower().replace(" ", "")


def make_accounts(rng):
    names = [f"{p} {s}" for p, s in itertools.product(PREFIXES, SUFFIXES)]
    rng.shuffle(names)
    accounts = []
    for i, name in enumerate(names[:N_ACCOUNTS]):
        owner = rng.choice(OWNERS)
        accounts.append({
            "account_id": f"ACC-{1000 + i}",
            "account_name": name,
            "account_owner": owner,
            "owner_email": f"{slug(owner)}@ourco.example",
            "billing_contact_email": f"ap@{slug(name)}.example",
        })
    return accounts


def log_uniform(rng, lo, hi):
    return int(10 ** rng.uniform(math.log10(lo), math.log10(hi)))


def draw_cents(rng):
    r = rng.random()
    if r < 0.012:
        return rng.randint(1, 99)
    if r < 0.03:
        return rng.randint(100, 499)
    if r < 0.70:
        return log_uniform(rng, 500, 50_000)
    if r < 0.985:
        return log_uniform(rng, 50_000, 10_000_000)
    return log_uniform(rng, 10_000_000, 250_000_000)


def draw_days_past_due(rng):
    r = rng.random()
    if r < 0.5:
        return rng.randint(0, 45)
    if r < 0.8:
        return rng.randint(46, 90)
    return rng.randint(91, 180)


def draw_status(rng, cents):
    r = rng.random()
    if r < 0.35:
        return "paid"
    if r < 0.80:
        return "open"
    if r < 0.92:
        return "partially_paid" if cents >= 100 else "open"
    return "disputed"


def draw_touch(rng, cents, issue, as_of, status):
    if status == "paid":
        if rng.random() < 0.5:
            return None
        d = as_of - timedelta(days=rng.randint(0, 120))
        return d if d >= issue else None
    dollars = cents / 100
    if dollars >= 5000:
        p_never, lo, hi = 0.05, 0, 10
    elif dollars >= 500:
        p_never, lo, hi = 0.2, 0, 30
    else:
        p_never, lo, hi = 0.4, 0, 60
    if rng.random() < p_never:
        return None
    d = as_of - timedelta(days=rng.randint(lo, hi))
    return d if d >= issue else None


def make_invoice(rng, invoice_id, account, as_of, days_past_due=None):
    cents = draw_cents(rng)
    status = draw_status(rng, cents)
    dpd = draw_days_past_due(rng) if days_past_due is None else days_past_due
    due = as_of - timedelta(days=dpd)
    issue = due - timedelta(days=rng.choice((30, 30, 45, 60)))
    if status == "paid":
        paid = cents
    elif status == "partially_paid":
        paid = int(cents * rng.uniform(0.2, 0.8))
    else:
        paid = 0
    dispute = status == "disputed" or (status == "open" and rng.random() < 0.02)
    return {
        **account,
        "invoice_id": invoice_id,
        "cents": cents,
        "paid_cents": paid,
        "status": status,
        "issue": issue,
        "due": due,
        "touch": draw_touch(rng, cents, issue, as_of, status),
        "dispute": dispute,
    }


def note_row(rng, inv, when):
    text = rng.choice(NOTE_TEMPLATES).format(owner=inv["account_owner"].split()[0])
    return {"invoice_id": inv["invoice_id"], "collector": rng.choice(COLLECTORS), "touch_date": when, "note": text}


def make_notes(rng, invoices, as_of):
    notes = []
    for inv in invoices:
        if inv["status"] == "paid":
            continue
        dollars = inv["cents"] / 100
        p = 0.5 if dollars >= 5000 else 0.35 if dollars >= 500 else 0.18
        if rng.random() >= p:
            continue
        base = inv["touch"] or min(inv["issue"] + timedelta(days=rng.randint(5, 20)), as_of)
        for _ in range(rng.choice((1, 1, 2))):
            notes.append(note_row(rng, inv, max(base - timedelta(days=rng.randint(0, 3)), inv["issue"])))
    return notes


def add_contradictions(rng, invoices, notes, as_of):
    # Sheet says touched this week, Salesforce says weeks ago.
    pool = [i for i in invoices if i["status"] != "paid" and i["cents"] < 500_000 and (as_of - i["issue"]).days > 35]
    for inv in rng.sample(pool, 500):
        inv["touch"] = as_of - timedelta(days=rng.randint(15, 30))
        notes.append(note_row(rng, inv, as_of - timedelta(days=rng.randint(0, 6))))
    # Both sources stale but far apart, so these still queue and carry the conflict along.
    pool = [i for i in invoices if i["status"] != "paid" and i["cents"] < 50_000 and (as_of - i["issue"]).days > 60]
    for inv in rng.sample(pool, 100):
        inv["touch"] = as_of - timedelta(days=rng.randint(35, 60))
        notes.append(note_row(rng, inv, as_of - timedelta(days=rng.randint(9, 20))))


def slack_ts(d, rng):
    return f"{(d - date(1970, 1, 1)).days * 86400 + rng.randint(0, 86399)}.{rng.randint(0, 999999):06d}"


def make_thread(rng, inv, as_of):
    last = max(as_of - timedelta(days=rng.randint(1, 45)), inv["issue"])
    collector = rng.choice(COLLECTORS)
    balance = (inv["cents"] - inv["paid_cents"]) / 100
    messages = []
    k = rng.randint(2, 4)
    for j in range(k):
        d = max(last - timedelta(days=(k - 1 - j) * rng.randint(0, 3)), inv["issue"])
        if j % 2 == 0:
            user = collector
            text = rng.choice(COLLECTOR_SLACK).format(
                account=inv["account_name"], invoice_id=inv["invoice_id"],
                balance=f"{balance:,.2f}", days=(as_of - inv["due"]).days,
            )
        else:
            user = inv["account_owner"]
            text = rng.choice(OWNER_SLACK)
        messages.append({"user": user, "date": d.isoformat(), "ts": slack_ts(d, rng), "text": text})
    return {
        "invoice_id": inv["invoice_id"],
        "channel": rng.choice(CHANNELS),
        "thread_ts": messages[0]["ts"],
        "last_message_date": last.isoformat(),
        "messages": messages,
    }


def make_slack(rng, invoices, as_of, n=300):
    pool = [i for i in invoices if i["status"] != "paid"]
    return [make_thread(rng, inv, as_of) for inv in rng.sample(pool, n)]


def build_world(seed=SEED, as_of=AS_OF):
    rng = random.Random(seed)
    accounts = make_accounts(rng)
    weights = [rng.paretovariate(1.3) for _ in accounts]
    picks = rng.choices(accounts, weights=weights, k=N_INVOICES)
    invoices = [make_invoice(rng, f"INV-{100000 + i}", acct, as_of) for i, acct in enumerate(picks)]
    notes = make_notes(rng, invoices, as_of)
    add_contradictions(rng, invoices, notes, as_of)
    slack = make_slack(rng, invoices, as_of)
    return accounts, invoices, notes, slack


def sf_row(inv):
    return {
        "invoice_id": inv["invoice_id"],
        "account_id": inv["account_id"],
        "account_name": inv["account_name"],
        "account_owner": inv["account_owner"],
        "owner_email": inv["owner_email"],
        "billing_contact_email": inv["billing_contact_email"],
        "currency": "USD",
        "amount": f"{inv['cents'] / 100:.2f}",
        "amount_paid": f"{inv['paid_cents'] / 100:.2f}",
        "status": inv["status"],
        "issue_date": inv["issue"].isoformat(),
        "due_date": inv["due"].isoformat(),
        "last_touch_date": inv["touch"].isoformat() if inv["touch"] else "",
        "dispute_flag": "true" if inv["dispute"] else "false",
    }


def write_bundle(folder, invoices, notes, slack, manifest):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "salesforce_invoices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SF_FIELDS)
        w.writeheader()
        w.writerows(sf_row(i) for i in invoices)
    with open(folder / "collector_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        w.writeheader()
        w.writerows({**n, "touch_date": n["touch_date"].isoformat()} for n in notes)
    with open(folder / "slack_threads.json", "w", encoding="utf-8") as f:
        json.dump(slack, f, indent=1)
    manifest["counts"] = {"invoices": len(invoices), "sheet_notes": len(notes), "slack_threads": len(slack)}
    with open(folder / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def build_delta(invoices, accounts, as_of, seed, notes=(), slack=()):
    rng = random.Random(seed)
    stale_before = as_of - timedelta(days=8)

    def stale(i):
        return i["touch"] is None or i["touch"] <= stale_before

    # Invoices that were 28-29 days past due in the full export and cross 30 days now.
    aging = [
        i for i in invoices
        if i["status"] != "paid" and 28 <= (AS_OF - i["due"]).days <= 29
        and i["cents"] - i["paid_cents"] <= 50_000 and stale(i)
    ]
    aging = rng.sample(aging, min(12, len(aging)))

    # Small queued-looking invoices that got paid since the last export.
    paid = [
        i for i in invoices
        if i["status"] == "open" and (AS_OF - i["due"]).days >= 45 and i["cents"] <= 50_000 and stale(i)
        and i not in aging
    ]
    paid = [{**i, "status": "paid", "paid_cents": i["cents"], "touch": as_of - timedelta(days=1)} for i in rng.sample(paid, 10)]

    # The invoices the agent drafts first change under it, so revalidation has something real to withdraw:
    # paid, disputed, new billing contact, a collector's note, a partial payment.
    taken = {i["invoice_id"] for i in aging + paid}
    first = [i for i in first_in_line(invoices, notes, slack) if i["invoice_id"] not in taken][:5]
    changed, delta_notes = [], []
    if first:
        changed.append({**first[0], "status": "paid", "paid_cents": first[0]["cents"]})
    if len(first) > 1:
        changed.append({**first[1], "status": "disputed", "dispute": True})
    if len(first) > 2:
        changed.append({**first[2], "billing_contact_email": "accounts.payable@" + first[2]["billing_contact_email"].split("@", 1)[1]})
    if len(first) > 3:
        changed.append(first[3])
        delta_notes.append({"invoice_id": first[3]["invoice_id"], "collector": "Grace Kim",
                            "touch_date": as_of - timedelta(days=1), "note": "Spoke with AP, they are paying this Friday"})
    if len(first) > 4:
        changed.append({**first[4], "status": "partially_paid", "paid_cents": first[4]["cents"] // 2})

    fresh = [
        make_invoice(rng, f"INV-{100000 + N_INVOICES + n}", rng.choice(accounts), as_of, days_past_due=rng.randint(0, 10))
        for n in range(200 - len(aging) - len(paid) - len(changed))
    ]
    return aging + paid + changed + fresh, delta_notes


def first_in_line(invoices, notes, slack):
    """The agent-bucket invoices in the order triage queues them (oldest first, then largest)."""
    latest = {}
    for n in notes:
        latest[n["invoice_id"]] = max(latest.get(n["invoice_id"], n["touch_date"]), n["touch_date"])
    for t in slack:
        d = date.fromisoformat(t["last_message_date"])
        latest[t["invoice_id"]] = max(latest.get(t["invoice_id"], d), d)
    out = []
    for i in invoices:
        balance = i["cents"] - i["paid_cents"]
        touch = max([d for d in (i["touch"], latest.get(i["invoice_id"])) if d], default=None)
        if (i["status"] in ("open", "partially_paid") and not i["dispute"] and 500 < balance <= 50_000
                and (AS_OF - i["due"]).days >= 30 and (touch is None or (AS_OF - touch).days >= 8)):
            out.append(i)
    return sorted(out, key=lambda i: (-(AS_OF - i["due"]).days, -(i["cents"] - i["paid_cents"]), i["invoice_id"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", action="store_true", help="write a 200-invoice follow-up export to data/delta/")
    ap.add_argument("--out", type=Path, help="override the output folder")
    args = ap.parse_args()

    accounts, invoices, notes, slack = build_world()

    if args.delta:
        as_of = AS_OF + timedelta(days=2)
        delta, delta_notes = build_delta(invoices, accounts, as_of, SEED + 1, notes, slack)
        folder = args.out or ROOT / "data" / "delta"
        write_bundle(folder, delta, delta_notes, [], {"kind": "delta", "as_of": as_of.isoformat(), "base_as_of": AS_OF.isoformat(), "seed": SEED + 1})
        print(f"delta: {len(delta)} invoices as of {as_of} -> {folder}; the first five agent invoices change "
              f"(paid, disputed, new contact, collector note, partial payment)")
        return

    folder = args.out or ROOT / "data" / "exports"
    write_bundle(folder, invoices, notes, slack, {"kind": "full", "as_of": AS_OF.isoformat(), "seed": SEED})
    print(f"full export: {len(invoices)} invoices, {len(notes)} sheet notes, {len(slack)} slack threads as of {AS_OF} -> {folder}")


if __name__ == "__main__":
    main()
