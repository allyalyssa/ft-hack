#!/usr/bin/env python3
"""Write the synthetic collections exports.

    python3 data/generate.py           20,000 invoices into data/exports/
    python3 data/generate.py --delta   the same 20,000 plus 200 new ones into data/exports/delta/

Seed 7, so every run writes the same files and the deck numbers hold.
Standard library only. File formats follow CONTRACT.md.
"""
import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 7
AS_OF = date(2026, 10, 25)
N_INVOICES = 20_000
N_ACCOUNTS = 1_200
N_DELTA = 200
N_DELTA_CROSSED = 60  # delta invoices sitting at 30 or 31 days past due

HERE = Path(__file__).resolve().parent
EXPORTS = HERE / "exports"

SF_COLUMNS = [
    "invoice_id", "account_id", "account_name", "account_owner", "account_owner_email",
    "amount", "currency", "invoice_date", "due_date", "status", "last_touch_date",
    "last_touch_channel", "contact_name", "contact_email", "contact_slack",
]
SHEET_COLUMNS = ["invoice_id", "collector", "note", "note_date", "promise_to_pay_date", "dispute_flag"]

POLICY_YAML = (
    f"as_of: {AS_OF.isoformat()}\n"
    "min_days_past_due: 30\n"
    "stale_touch_days: 8\n"
    "write_off_max_amount: 25.00\n"
    "agent_max_amount: 500.00\n"
    "currency: USD\n"
)

# Account owners sell, collectors chase, the AR manager signs off write-offs.
OWNERS = [
    "Priya Raman", "Marcus Bell", "Elena Sokolova", "David Okafor",
    "Hannah Lindqvist", "Tomas Herrera", "Grace Whitfield", "Kenji Watanabe",
    "Aisha Mensah", "Ryan Kowalski", "Sofia Marchetti", "Ben Adeyemi",
]
OWNER_WEIGHTS = [14, 13, 12, 11, 10, 10, 9, 8, 8, 7, 6, 5]
COLLECTORS = ["Jordan Reyes", "Maya Thompson", "Luis Ortega", "Nina Patel", "Chris Donovan"]
MANAGER = "Dana Whitaker"

STEMS = [
    "Alder", "Amberline", "Anchorpoint", "Applewood", "Arcadia", "Ashford", "Aspen", "Bayside",
    "Beacon", "Birchwood", "Blackwater", "Bluebird", "Bluestem", "Boulder", "Brightwater",
    "Brookside", "Calder", "Canyon", "Cardinal", "Cascade", "Cedar Ridge", "Clearwater",
    "Cobalt", "Copperline", "Cornerstone", "Crescent", "Driftwood", "Eastgate", "Evergreen",
    "Fairview", "Falcon", "Fieldstone", "Foxglove", "Granite", "Greenleaf", "Harbor",
    "Hawthorne", "Highland", "Hillcrest", "Ironwood", "Juniper", "Keystone", "Kingsley",
    "Lakeshore", "Larkspur", "Lighthouse", "Maple", "Meadowbrook", "Meridian", "Millbrook",
    "Northgate", "Oakridge", "Old Mill", "Orchard", "Pinecrest", "Prairie", "Quarry",
    "Redwood", "Ridgeline", "Riverbend", "Rockport", "Saltwater", "Sandstone", "Sawtooth",
    "Silverleaf", "Southport", "Stonebridge", "Sunrise", "Tamarack", "Thornbury",
    "Timberline", "Trillium", "Twin Pines", "Valleyview", "Westbrook", "Whitecap", "Willow",
    "Windmere", "Woodland", "Yarrow",
]
INDUSTRIES = [
    "Logistics", "Foods", "Dental", "Analytics", "Supply", "Manufacturing", "Hospitality",
    "Health", "Builders", "Labs", "Software", "Freight", "Packaging", "Medical", "Brewing",
    "Outfitters", "Energy", "Design", "Robotics", "Media", "Partners", "Systems", "Pharmacy",
    "Veterinary", "Farms", "Textiles", "Marine", "Tooling", "Print", "Staffing",
]
FIRST_NAMES = [
    "Maria", "James", "Linda", "Robert", "Wei", "Fatima", "Carlos", "Olivia", "Ahmed", "Sarah",
    "Daniel", "Mei", "Rachel", "Kevin", "Laura", "Omar", "Emily", "Jason", "Ana", "Michael",
    "Jessica", "Andre", "Nicole", "Samuel", "Hiroshi", "Rosa", "Thomas", "Leila", "Patrick",
    "Megan", "Victor", "Chloe", "Ivan", "Diego", "Tara", "Peter", "Yuki", "Brandon", "Amara",
    "Lucas", "Natalie", "Raj", "Claire", "Martin", "Zoe", "Felix", "Ingrid", "Owen", "Hassan",
    "Priscilla",
]
LAST_NAMES = [
    "Chen", "Johnson", "Garcia", "Nguyen", "Smith", "Rossi", "Kim", "Brown", "Silva", "Murphy",
    "Novak", "Haddad", "Larsen", "Cohen", "Okoye", "Tanaka", "Fischer", "Walsh", "Dubois",
    "Moreno", "Ali", "Baker", "Hughes", "Ivanova", "Jensen", "Kaur", "Lopez", "Mendes",
    "Nakamura", "Oliveira", "Park", "Quinn", "Schmidt", "Torres", "Usman", "Vasquez", "Wright",
    "Yamada", "Zhang", "Adams", "Becker", "Costa", "Duarte", "Evans", "Foster", "Grant",
    "Hayes", "Iqbal", "Kelly", "Lambert", "Mason", "Nash", "Ortiz", "Price", "Ramos", "Stone",
    "Turner",
]
ID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# Amount bands, as shares of all invoices: under $5, $5 to $500, $500 to $100k, over $100k.
BANDS = ["tiny", "small", "mid", "large"]
BAND_WEIGHTS = [3.0, 67.0, 28.5, 1.5]
PLAN_PRICES = [29, 49, 79, 99, 129, 149, 199, 249, 299, 399, 499]

# Days past due by band. Small invoices get older because nobody chases them.
AGE_RANGES = [(0, 29), (30, 59), (60, 89), (90, 119), (120, 180)]
AGE_WEIGHTS = {
    "tiny": [30, 20, 15, 15, 20],
    "small": [64, 17, 9, 5, 5],
    "mid": [62, 18, 9, 6, 5],
    "large": [70, 18, 7, 3, 2],
}
NEVER_TOUCHED = {"tiny": 0.72, "small": 0.40, "mid": 0.20, "large": 0.03}  # open invoices, no CRM touch
RECENT_TOUCH = {"tiny": 0.20, "small": 0.50, "mid": 0.60, "large": 0.85}   # touched ones, within 7 days
SHEET_RATE = {"tiny": 0.04, "small": 0.19, "mid": 0.44, "large": 0.92}     # touched ones with sheet notes

# One snapshot per export. The delta is a later snapshot on the same day.
SNAPSHOTS = {
    "full": ("2026-10-25T0600", "2026-10-25T06:00:00"),
    "delta": ("2026-10-25T1430", "2026-10-25T14:30:00"),
}

# Collector notes. The first channel word in each note tells triage the channel.
PHONE_NOTES = [
    "Called {first} in AP, left voicemail.",
    "Called AP line, no answer. Will try again next week.",
    "Spoke with {first}, says it is in the next payment run.",
    "Spoke with {first}, they need a W-9 before they can pay.",
    "Left voicemail for {first} about {inv}.",
    "Called {first}, invoice is waiting on a second approver.",
]
EMAIL_NOTES = [
    "Emailed {first} a copy of {inv}.",
    "Emailed reminder for {inv}, no reply yet.",
    "Sent statement with all open items to {email}.",
    "Sent second reminder, cc'd {owner_first}.",
    "Emailed {first} the remittance details again.",
    "Emailed {first} asking which PO number they need on the invoice.",
]
PORTAL_NOTES = [
    "Uploaded {inv} to the customer AP portal.",
    "Checked AP portal, invoice approved, waiting on their payment run.",
    "AP portal shows the invoice rejected for a missing PO number.",
]
PROMISE_NOTES = {
    "phone": "Spoke with {first}, promised payment by {ptp}.",
    "email": "{first} replied by email, payment scheduled for {ptp}.",
}
DISPUTE_NOTES = {
    "phone": [
        "Spoke with {first}, they dispute the overage charge on {inv}.",
        "Called {first}, they say a service credit was never applied. Disputed.",
    ],
    "email": [
        "Emailed with {first}, customer disputes the quantity billed. Sent to {owner_first}.",
        "{first} emailed that {inv} duplicates an earlier invoice. Disputed, checking with billing.",
    ],
    "portal": ["AP portal shows {inv} on hold, reason given: pricing dispute."],
}

# Slack threads: (who, text). Kinds that quote days past due need the invoice to be late already.
THREADS = {
    "owner_help": [
        ("collector", "@{owner_h} {account} still owes {amt} on {inv}, {dpd_at} days past due. "
                      "{first} hasn't answered two emails. Is there anyone else in their finance team I should try?"),
        ("owner", "I think {first} moved to another team. Try {alt_name}, they took over AP in the summer."),
        ("collector", "Thanks, emailing {alt_first} today."),
    ],
    "erp": [
        ("collector", "Anyone know why {account} stopped paying? {inv} is {dpd_at} days late and they used to pay on time."),
        ("owner", "They moved to a new ERP last month and their AP queue is backed up. I'll ask our champion there to nudge it."),
        ("collector", "Appreciate it."),
        ("manager", "If nothing lands by month end, loop me in."),
    ],
    "renewal": [
        ("owner", "@{coll_h} I have a renewal call with {account} on Thursday. Anything open I should raise?"),
        ("collector", "{inv} for {amt}, {dpd_at} days past due. No reply to the last reminder."),
        ("owner", "Got it, I'll bring it up."),
    ],
    "resend": [
        ("customer", "Hi, we can't find {inv} in our system. Can you resend it?"),
        ("collector", "Sure, sending it to {email} now. It's {amt}, due {due}."),
        ("customer", "Got it, thanks. It will go out in our next payment run."),
    ],
    "po": [
        ("collector", "Hi {first}, checking on {inv} ({amt}). Is anything holding it up?"),
        ("customer", "It's waiting on a PO match on our side. I'll chase it."),
        ("collector", "Thanks. Happy to resend the PO details if that helps."),
    ],
    "promise": [
        ("customer", "Payment for {inv} goes out on {ptp}."),
        ("collector", "Thanks {first}, noted."),
    ],
    "dispute": [
        ("owner", "Heads up, {account} is disputing {inv}. They say the usage overage was billed twice."),
        ("collector", "Ok, marking it disputed and pausing reminders. Can you confirm with their CSM?"),
        ("manager", "If they're right, let's get a credit memo out this week."),
    ],
    "tiny": [
        ("collector", "{inv} for {account} is {amt} and {dpd_at} days past due. Worth chasing?"),
        ("manager", "Anything under $25 falls under the write-off policy. Leave it for the batch."),
        ("collector", "Ok, leaving it."),
    ],
}
FOLLOW_UPS = [("collector", "Any update on this one?"), ("collector", "Bumping this, still open on our side.")]
LATE_KINDS = ["owner_help", "erp", "renewal", "resend", "po", "promise"]
EARLY_KINDS = ["resend", "po", "promise"]


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def dollars(cents):
    return f"${cents / 100:,.2f}"


def handle(name):
    first, last = name.split(" ", 1)
    return (first[0] + last.replace(" ", "")).lower()


def email_for(name):
    return name.lower().replace(" ", ".") + "@example.com"


def short_date(d):
    return f"{MONTHS[d.month - 1]} {d.day}"


def build_accounts(rng):
    combos = [(stem, industry) for stem in STEMS for industry in INDUSTRIES]
    accounts, seen = [], set()
    for stem, industry in rng.sample(combos, N_ACCOUNTS):
        suffix = rng.choices(["Inc", "LLC", "Co", "Corp", "Group", ""], [34, 34, 9, 9, 6, 8])[0]
        owner = rng.choices(OWNERS, OWNER_WEIGHTS)[0]
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        slug = "".join(ch for ch in (stem + industry).lower() if ch.isalnum())
        account_id = ""
        while not account_id or account_id in seen:
            account_id = "0018b000" + "".join(rng.choice(ID_CHARS) for _ in range(7))
        seen.add(account_id)
        accounts.append({
            "id": account_id,
            "name": " ".join(part for part in (stem, industry, suffix) if part),
            "slug": slug,
            "owner": owner,
            "collector": COLLECTORS[OWNERS.index(owner) % len(COLLECTORS)],
            "contact_first": first,
            "contact_name": f"{first} {last}",
            "contact_email": f"{first}.{last}@{slug}.example.com".lower(),
            "contact_slack": f"@{first}.{last}".lower(),
            "weight": rng.lognormvariate(0.0, 1.1),  # some accounts get billed far more often
        })
    return accounts


def draw_amount(rng, band):
    """Invoice amount in cents."""
    if band == "tiny":
        return rng.randint(1, 99) if rng.random() < 1 / 3 else rng.randint(100, 499)
    if band == "small":
        if rng.random() < 0.3:
            return rng.choice(PLAN_PRICES) * 100
        while True:
            cents = round(rng.lognormvariate(math.log(110.0), 0.95) * 100)
            if 500 <= cents <= 50_000:
                return cents
    if band == "mid":
        while True:
            cents = round(rng.lognormvariate(math.log(3_500.0), 1.15) * 100)
            if 50_000 < cents <= 10_000_000:
                return cents
    amount = 100_000.0 * (1.0 - rng.random()) ** (-1 / 1.5)  # Pareto tail above $100k
    return min(max(round(amount * 100), 10_000_001), 240_000_000)


def draw_status(rng, band):
    r = rng.random()
    if band == "tiny":  # nobody part-pays a $0.37 invoice
        return "paid" if r < 0.10 else "disputed" if r < 0.11 else "open"
    if r < 0.13:
        return "paid"
    if r < 0.17:
        return "disputed"
    if r < 0.25:
        return "partially_paid"
    return "open"


def draw_touch(rng, band, status, age):
    """Salesforce last touch as (date or None, channel). age is days since the invoice date."""
    never = 0.25 if status == "paid" else NEVER_TOUCHED[band]
    if rng.random() < never:
        return None, ""
    if rng.random() < RECENT_TOUCH[band]:
        days = rng.randint(0, 7)
    else:
        days = 8 + int(rng.expovariate(1 / 16))
    channel = rng.choices(["email", "phone", "portal"], [60, 30, 10])[0]
    return AS_OF - timedelta(days=min(days, age)), channel


def new_invoice(rng, account, band, dpd=None, status=None):
    cents = draw_amount(rng, band)
    status = status or draw_status(rng, band)
    if dpd is None:
        low, high = rng.choices(AGE_RANGES, AGE_WEIGHTS[band])[0]
        dpd = rng.randint(low, high)
    terms = rng.choices([30, 15, 45, 60], [70, 10, 10, 10])[0]
    due = AS_OF - timedelta(days=dpd)
    inv = {"account": account, "band": band, "cents": cents, "status": status,
           "dpd": dpd, "due": due, "issued": due - timedelta(days=terms)}
    inv["touch"], inv["channel"] = draw_touch(rng, band, status, dpd + terms)
    return inv


def build_invoices(rng, accounts):
    """Every account gets at least one invoice; the rest follow account activity."""
    weights = [a["weight"] for a in accounts]
    picks = list(accounts) + rng.choices(accounts, weights, k=N_INVOICES - len(accounts))
    rng.shuffle(picks)
    bands = rng.choices(BANDS, BAND_WEIGHTS, k=N_INVOICES)
    return [new_invoice(rng, account, band) for account, band in zip(picks, bands)]


def build_delta_invoices(rng, accounts):
    """The 200 new invoices. The first 60 just crossed 30 days past due and mostly went quiet."""
    weights = [a["weight"] for a in accounts]
    invoices = []
    for n in range(N_DELTA):
        account = rng.choices(accounts, weights)[0]
        if n < N_DELTA_CROSSED:
            band = rng.choices(["tiny", "small", "mid"], [8, 75, 17])[0]
            status = rng.choices(["open", "partially_paid", "disputed"], [85, 8, 7])[0]
            inv = new_invoice(rng, account, band, dpd=rng.choice([30, 30, 30, 31]), status=status)
            r = rng.random()
            if r < 0.50:
                inv["touch"], inv["channel"] = None, ""
            else:
                days = rng.randint(8, 28) if r < 0.85 else rng.randint(0, 7)
                inv["touch"] = AS_OF - timedelta(days=days)
                inv["channel"] = rng.choices(["email", "phone", "portal"], [60, 30, 10])[0]
        else:
            band = rng.choices(BANDS, BAND_WEIGHTS)[0]
            inv = new_invoice(rng, account, band, dpd=rng.randint(0, 29))
        invoices.append(inv)
    return invoices


def pin(inv, cents, dpd, touch_days, channel=""):
    terms = (inv["due"] - inv["issued"]).days
    inv.update(cents=cents, dpd=dpd, status="open", pinned=True, due=AS_OF - timedelta(days=dpd))
    inv["issued"] = inv["due"] - timedelta(days=terms)
    inv["touch"] = None if touch_days is None else AS_OF - timedelta(days=touch_days)
    inv["channel"] = channel if touch_days is not None else ""


def pin_showcase(invoices):
    """Fix the invoices the demo talks about, so they exist on every run."""
    def first_open(band, skip=0):
        return [i for i in invoices if i["band"] == band and i["status"] == "open"][skip]
    picks = [first_open("tiny"), first_open("tiny", 1), first_open("small"), first_open("large")]
    pin(picks[0], 37, 95, None)                     # $0.37, never chased
    pin(picks[1], 112, 64, 41, "email")             # $1.12, one email six weeks ago
    pin(picks[2], 4_000, 47, None)                  # $40.00, lost in the pile
    pin(picks[3], 14_000_000, 45, 2, "phone")       # $140,000.00, called two days ago
    return picks


def assign_ids(invoices, start):
    """Invoice numbers run in invoice-date order, like a billing system issues them."""
    order = sorted(range(len(invoices)), key=lambda n: (invoices[n]["issued"], n))
    for offset, n in enumerate(order):
        invoices[n]["id"] = f"INV-{start + offset:05d}"
    invoices.sort(key=lambda inv: inv["id"])


def note_text(rng, inv, channel, when, dispute):
    account = inv["account"]
    fill = {"first": account["contact_first"], "inv": inv["id"],
            "email": account["contact_email"], "owner_first": account["owner"].split()[0]}
    if dispute:
        return rng.choice(DISPUTE_NOTES[channel]).format(**fill), None
    if channel in PROMISE_NOTES and rng.random() < 0.18:
        promised = when + timedelta(days=rng.randint(3, 21))
        return PROMISE_NOTES[channel].format(ptp=short_date(promised), **fill), promised
    pool = {"phone": PHONE_NOTES, "email": EMAIL_NOTES, "portal": PORTAL_NOTES}[channel]
    return rng.choice(pool).format(**fill), None


def notes_for(rng, inv, latest, latest_channel, dispute):
    """1 to 4 notes, oldest first, the last one dated `latest`."""
    count = rng.choices([1, 2, 3, 4], [45, 30, 17, 8])[0]
    dates = [latest]
    for _ in range(count - 1):
        earlier = dates[-1] - timedelta(days=rng.randint(4, 16))
        if earlier < inv["issued"]:
            break
        dates.append(earlier)
    dates.reverse()
    collector = inv["account"]["collector"] if rng.random() < 0.9 else rng.choice(COLLECTORS)
    rows = []
    for n, when in enumerate(dates):
        last = n == len(dates) - 1
        if last and latest_channel:
            channel = latest_channel
        else:
            channel = rng.choices(["email", "phone", "portal"], [55, 35, 10])[0]
        text, promised = note_text(rng, inv, channel, when, last and dispute)
        rows.append({
            "invoice_id": inv["id"], "collector": collector, "note": text,
            "note_date": when.isoformat(),
            "promise_to_pay_date": promised.isoformat() if promised else "",
            "dispute_flag": "true" if (last and dispute) else "false",
        })
    return rows


def build_sheet(rng, invoices, n_newer, n_sheet_disputes):
    """Collector sheet rows. The latest note matches the Salesforce touch, except on
    n_newer invoices where the collector logged a touch Salesforce never got."""
    free = [i for i in invoices if not i.get("pinned")]
    in_reach = [i for i in free if i["status"] != "paid" and i["cents"] <= 50_000 and i["dpd"] >= 30]
    others = [i for i in free if i["status"] != "paid" and not (i["cents"] <= 50_000 and i["dpd"] >= 30)]
    n_reach = min(round(n_newer * 0.7), len(in_reach))
    rows = []
    for inv in rng.sample(in_reach, n_reach) + rng.sample(others, min(n_newer - n_reach, len(others))):
        age = (AS_OF - inv["issued"]).days
        days = rng.randint(1, 6) if rng.random() < 0.6 else rng.randint(8, 20)
        latest = AS_OF - timedelta(days=min(days, age))
        if inv["touch"] is not None and inv["touch"] >= latest:
            older = latest - timedelta(days=rng.randint(12, 30))
            if older < inv["issued"]:
                inv["touch"], inv["channel"] = None, ""
            else:
                inv["touch"] = older
        inv["sheet"] = "newer"
        rows += notes_for(rng, inv, latest, None, inv["status"] == "disputed")

    consistent = [i for i in free if not i.get("sheet") and i["touch"] is not None
                  and rng.random() < SHEET_RATE[i["band"]]]
    # Disputes the collector heard about and Salesforce still shows as open.
    pool = [i for i in consistent if i["status"] in ("open", "partially_paid")
            and i["cents"] <= 50_000 and i["dpd"] >= 30]
    for inv in rng.sample(pool, min(n_sheet_disputes, len(pool))):
        inv["sheet_dispute"] = True
    for inv in consistent:
        inv["sheet"] = "same"
        dispute = inv["status"] == "disputed" or inv.get("sheet_dispute", False)
        rows += notes_for(rng, inv, inv["touch"], inv["channel"], dispute)
    rows.sort(key=lambda row: (row["note_date"], row["invoice_id"]))
    return rows


def make_thread(rng, inv):
    account = inv["account"]
    age = (AS_OF - inv["issued"]).days
    start_days = rng.randint(1, max(1, min(50, age, inv["dpd"] - 3)))
    dpd_at = inv["dpd"] - start_days
    if inv["band"] == "tiny" and dpd_at >= 3:
        kind = "tiny"
    elif inv["status"] == "disputed":
        kind = "dispute"
    else:
        kind = rng.choice(LATE_KINDS if dpd_at >= 3 else EARLY_KINDS)
    script = list(THREADS[kind])
    follow_ups = rng.choices([0, 1, 2], [70, 22, 8])[0]
    if kind != "tiny":  # the manager already said to leave it
        script += FOLLOW_UPS[:min(follow_ups, 5 - len(script))]

    day = AS_OF - timedelta(days=start_days)
    when = datetime(day.year, day.month, day.day, rng.randint(8, 16), rng.randint(0, 59))
    limit = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 59)
    alt_first, alt_last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    fill = {
        "account": account["name"], "inv": inv["id"], "amt": dollars(inv["cents"]),
        "dpd_at": dpd_at, "first": account["contact_first"], "email": account["contact_email"],
        "owner_h": handle(account["owner"]), "coll_h": handle(account["collector"]),
        "alt_name": f"{alt_first} {alt_last}", "alt_first": alt_first,
        "due": short_date(inv["due"]),
        "ptp": short_date(day + timedelta(days=rng.randint(3, 14))),
    }
    users = {"collector": handle(account["collector"]), "owner": handle(account["owner"]),
             "manager": handle(MANAGER), "customer": account["contact_slack"].lstrip("@")}
    messages = []
    for n, (who, text) in enumerate(script):
        if n:
            if rng.random() < 0.7:
                step = timedelta(minutes=rng.randint(4, 180))
            else:
                step = timedelta(days=rng.randint(1, 2), minutes=rng.randint(0, 120))
            if when + step > limit:
                step = timedelta(minutes=rng.randint(4, 180))
            when += step
        messages.append({"ts": when.isoformat(), "user": users[who], "text": text.format(**fill)})
    if kind == "dispute":
        channel = "#ar-disputes"
    elif kind in EARLY_KINDS:
        channel = "#ext-" + account["slug"]  # shared channel with the customer
    else:
        channel = "#ar-collections"
    return {"invoice_id": inv["id"], "channel": channel, "messages": messages}


def build_slack(rng, invoices, per_band):
    free = [i for i in invoices if not i.get("pinned") and i["status"] != "paid"]
    chosen = []
    for band, count in per_band.items():
        pool = [i for i in free if i["band"] == band]
        chosen += rng.sample(pool, min(count, len(pool)))
    chosen.sort(key=lambda inv: inv["id"])
    return [make_thread(rng, inv) for inv in chosen]


def write_export(out_dir, invoices, sheet_rows, threads, snapshot):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "salesforce_invoices.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(SF_COLUMNS)
        for inv in invoices:
            a = inv["account"]
            writer.writerow([
                inv["id"], a["id"], a["name"], a["owner"], email_for(a["owner"]),
                money(inv["cents"]), "USD", inv["issued"].isoformat(), inv["due"].isoformat(),
                inv["status"], inv["touch"].isoformat() if inv["touch"] else "", inv["channel"],
                a["contact_name"], a["contact_email"], a["contact_slack"],
            ])
    with open(out_dir / "collector_sheet.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sheet_rows)
    (out_dir / "slack_threads.json").write_text(json.dumps(threads, indent=2) + "\n", encoding="utf-8")
    (out_dir / "policy.yaml").write_text(POLICY_YAML, encoding="utf-8")
    snapshot_id, exported_at = snapshot
    manifest = {"snapshot_id": snapshot_id, "exported_at": exported_at,
                "source_files": ["salesforce_invoices.csv", "collector_sheet.csv", "slack_threads.json"]}
    (out_dir / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def summarize(out_dir, invoices, sheet_rows, threads, showcase, snapshot_id, new=()):
    n = len(invoices)
    still_open = [i for i in invoices if i["status"] != "paid"]

    def share(count, total):
        return f"{100 * count / total:.1f}%"

    statuses = ", ".join(f"{s} {sum(1 for i in invoices if i['status'] == s)}"
                         for s in ("open", "partially_paid", "disputed", "paid"))
    try:
        where = out_dir.relative_to(HERE.parent)
    except ValueError:
        where = out_dir
    lines = [
        f"wrote {where} (snapshot {snapshot_id}): {n} invoices, "
        f"{len({i['account']['id'] for i in invoices})} accounts, "
        f"{len({i['account']['owner'] for i in invoices})} account owners",
        f"  amounts: {share(sum(1 for i in invoices if i['cents'] <= 50_000), n)} at or under $500, "
        f"{share(sum(1 for i in invoices if i['cents'] < 500), n)} under $5 "
        f"({sum(1 for i in invoices if i['cents'] < 100)} under $1), "
        f"{sum(1 for i in invoices if i['cents'] > 10_000_000)} over $100,000",
        f"  status: {statuses}",
        f"  open with no salesforce touch: "
        f"{share(sum(1 for i in still_open if i['touch'] is None), len(still_open))}",
        f"  collector sheet: {len(sheet_rows)} notes on {len({r['invoice_id'] for r in sheet_rows})} invoices, "
        f"{sum(1 for i in invoices if i.get('sheet') == 'newer')} newer than salesforce, "
        f"{sum(1 for i in invoices if i.get('sheet_dispute'))} disputes only in the sheet",
        f"  slack: {len(threads)} threads, {sum(len(t['messages']) for t in threads)} messages",
    ]
    if new:
        lines.append(f"  new since the full export: {len(new)} invoices, {new[0]['id']} to {new[-1]['id']}, "
                     f"{sum(1 for i in new if i['dpd'] in (30, 31))} of them at 30 or 31 days past due")
    for inv in showcase:
        touch = "never touched" if inv["touch"] is None else f"touched {(AS_OF - inv['touch']).days} days ago"
        lines.append(f"  showcase {inv['id']}: {dollars(inv['cents'])}, {inv['dpd']} days past due, {touch}")
    print("\n".join(lines))


def full_world(seed=SEED, invoice_start=1):
    rng = random.Random(seed)
    accounts = build_accounts(rng)
    invoices = build_invoices(rng, accounts)
    showcase = pin_showcase(invoices)
    assign_ids(invoices, start=invoice_start)
    sheet_rows = build_sheet(rng, invoices, n_newer=200, n_sheet_disputes=40)
    threads = build_slack(rng, invoices, {"large": 60, "mid": 130, "small": 95, "tiny": 15})
    return invoices, sheet_rows, threads, showcase


def new_since_full(seed=SEED, invoice_start=1):
    """The 200 invoices the 14:30 snapshot adds, with their sheet rows and Slack threads.
    A second stream from the same seed replays the accounts, so they land on existing customers."""
    rng = random.Random(seed)
    accounts = build_accounts(rng)
    invoices = build_delta_invoices(rng, accounts)
    assign_ids(invoices, start=invoice_start + N_INVOICES)
    sheet_rows = build_sheet(rng, invoices, n_newer=3, n_sheet_disputes=1)
    threads = build_slack(rng, invoices, {"mid": 2, "small": 4})
    return invoices, sheet_rows, threads


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the synthetic collections exports (seed 7).")
    parser.add_argument("--delta", action="store_true",
                        help="write the later snapshot (full export plus 200 new invoices) to data/exports/delta/")
    parser.add_argument("--seed", type=int, default=SEED, help="reproducible random seed (default: 7)")
    parser.add_argument("--invoice-start", type=int, default=1,
                        help="first invoice number; use a new range for an additional batch")
    parser.add_argument("--out", type=Path, help="separate export folder; defaults to data/exports/")
    args = parser.parse_args(argv)
    if args.invoice_start < 1:
        parser.error("--invoice-start must be positive")

    invoices, sheet_rows, threads, showcase = full_world(args.seed, args.invoice_start)
    new = []
    if args.delta:  # a later export: the whole list again, plus what arrived since
        new, new_rows, new_threads = new_since_full(args.seed, args.invoice_start)
        invoices = invoices + new
        sheet_rows = sorted(sheet_rows + new_rows, key=lambda row: (row["note_date"], row["invoice_id"]))
        threads = threads + new_threads
    out_dir, snapshot = (EXPORTS / "delta", SNAPSHOTS["delta"]) if args.delta else (EXPORTS, SNAPSHOTS["full"])
    if args.out:
        out_dir = args.out
    if args.seed != SEED or args.invoice_start != 1:
        snapshot = (f"{snapshot[0]}-s{args.seed}-i{args.invoice_start}", snapshot[1])
    write_export(out_dir, invoices, sheet_rows, threads, snapshot)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(synthetic=True, seed=args.seed, invoice_start=args.invoice_start,
                    invoice_count=len(invoices))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    summarize(out_dir, invoices, sheet_rows, threads, showcase, snapshot[0], new)


if __name__ == "__main__":
    main()
