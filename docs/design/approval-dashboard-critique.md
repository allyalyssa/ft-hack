# Approval dashboard: design critique (Impeccable), 2026-10-25

For channel/dashboard.py. Run with Impeccable's critique method: an independent design review plus its detector
(`npx -y impeccable detect --json` on the saved queue, Salesforce-cases and invoice pages). Score 19/40.
The palette and rules to use are in `docs/design/greenbar.css`; the dispute desk (`disputes/board.py`) shows them on
a real screen.

## Fix first

1. **P0, a false sentence on the invoice page.** The case summary says "180 days past due with no prior
   follow-up" while the same page shows "Last touch 20d ago (salesforce)", and the owner note says the last touch was
   October 5. Build that clause from `last_touch_date` / `never_touched`, or drop it. A judge reading the page will
   catch it.
2. **P1, contrast.** The muted grey `#8592a5` on white is 3.2:1 (needs 4.5:1). It accounts for all 10 detector
   findings across the queue, cases and invoice pages. Greenbar's `--ink-muted` (`#566070`, 6.4:1) is the drop-in.
3. **P1, the six equal number cards.** The strongest number, 533 collector hours, sits sixth. Lead with one figure
   or one plain sentence of real numbers; the rest as a quiet line.
4. **P1, colours outside the system.** The Approve button is green and the "edited" pill is purple. In Greenbar,
   green means money recovered and actions are ledger blue (`--ledger`). Make Reject a quiet secondary button and the
   pills into stamps (`--*-soft` fill, coloured text).
5. **P1, approving is slow and unprotected.** Every approval needs a typed name in a field labelled only by its
   placeholder; there is no undo and Reject asks for no reason. Remember the reviewer (the cookie is already read),
   give the field a real label, show an undo message after Approve and Reject, and offer a short reason menu on Reject.

## Then

- No `:focus-visible`, `:active` or `:disabled` styles, and no loading state on submit; rows navigate by a `tr`
  onclick, so the keyboard can't reach them.
- The invoice page skips from `<h1>` to `<h3>` (detector `skipped-heading`).
- Tabs mix buckets (Agent works it, Write off, With a person) with statuses (Awaiting, Approved, Rejected); split
  them into two controls.
- Three date formats on one page ("April 28, 2026", "2026-11-02", "20d ago"); pick one.
- Copy shows code values ("collections outreach", "open", "agent"), a file path (`agent/crm_mock/crm.sqlite`) and
  "1 cases".
- Monospace on the invoice-number heading and the email edit box; keep mono for amounts and IDs in tables.
- For the demo, draft at least 20 invoices before judges arrive: "1 awaiting" in a page of white space reads as an
  agent that did nothing.
