# writeoff_memo

An internal write-off memo for the audit trail. Plain text. Use the labels exactly as written, in this order, one per line. The Reason line uses only this invoice's collector notes and last touch. Do not copy a reason from an example, and do not estimate what collection would cost.

## Shape
Write-off memo: [invoice], [customer]
Amount: [amount]
Due date: [due date] ([days] days past due)
Last touch: [date and channel, or "none on record"]
Policy clause: policy.yaml allows a write-off when the amount is at or under write_off_max_amount ([write_off_max_amount]) and the invoice is at least min_days_past_due ([min_days_past_due]) days past due. This invoice meets both.
Reason: [one or two sentences from the facts on how the balance arose, or "No collector note on record."]
Recommendation: Write off [amount] and close the balance in Salesforce.
Prepared by: Collections agent, [today's date], draft for review
Approver: ____________________  Date: ____________

## Example 1
Facts: invoice INV-202871, Westbrook Tutoring, $12.50, due July 6, 2026, 77 days past due, last touch August 14, 2026 by email (Salesforce), collector note: "Paid by wire, $12.50 short, bank fee", write_off_max_amount $25.00, min_days_past_due 30, today September 21, 2026.

Write-off memo: INV-202871, Westbrook Tutoring
Amount: $12.50
Due date: July 6, 2026 (77 days past due)
Last touch: August 14, 2026, email
Policy clause: policy.yaml allows a write-off when the amount is at or under write_off_max_amount ($25.00) and the invoice is at least min_days_past_due (30) days past due. This invoice meets both.
Reason: The customer paid by wire and the payment arrived $12.50 short, which the collector traced to a bank fee.
Recommendation: Write off $12.50 and close the balance in Salesforce.
Prepared by: Collections agent, September 21, 2026, draft for review
Approver: ____________________  Date: ____________

## Example 2
Facts: invoice INV-203310, Harbor Lane Bakery, $1.12, due May 27, 2026, 117 days past due, no touch on record, no collector notes, write_off_max_amount $25.00, min_days_past_due 30, today September 21, 2026.

Write-off memo: INV-203310, Harbor Lane Bakery
Amount: $1.12
Due date: May 27, 2026 (117 days past due)
Last touch: none on record
Policy clause: policy.yaml allows a write-off when the amount is at or under write_off_max_amount ($25.00) and the invoice is at least min_days_past_due (30) days past due. This invoice meets both.
Reason: No collector note on record. The balance has had no touch since it was issued.
Recommendation: Write off $1.12 and close the balance in Salesforce.
Prepared by: Collections agent, September 21, 2026, draft for review
Approver: ____________________  Date: ____________
