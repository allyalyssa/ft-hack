"""The board must search the whole export and preserve financial totals."""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import server


class LedgerTests(unittest.TestCase):
    def setUp(self):
        def invoice(name, amount, paid, due, status="open", owner="Elena"):
            return {"account_name": name, "account_owner": owner, "amount": amount,
                    "amount_paid": paid, "due_date": due, "status": status}
        self.world = SimpleNamespace(
            load=lambda: None, as_of=date(2026, 10, 25), touch={}, slack={},
            items={"INV-100001": {"bucket": "agent"}},
            invoices={
                "INV-100001": invoice("Onyx Vision", 120, 20, "2026-09-25"),
                "INV-100002": invoice("Cinder Works", 50, 50, "2026-04-01", "paid"),
                "INV-100003": invoice("Cinder Works", 40, 0, "2026-10-20", "disputed", "Sam"),
                "INV-100004": invoice("Future Account", 10, 0, "2026-11-01"),
            })
        self.patch = patch.object(server, "WORLD", self.world)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_whole_export_and_totals(self):
        result = server.ledger({})
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["counts"], {"all": 4, "open": 3, "overdue": 2,
                                           "disputed": 1, "queued": 1, "paid": 1})
        self.assertEqual(result["summary"], {"outstanding": 150, "overdue": 140, "paid": 70})
        self.assertEqual(result["rows"][0]["invoice_id"], "INV-100001")

    def test_search_reaches_records_outside_queue(self):
        result = server.ledger({"q": ["cINder"], "owner": ["Sam"]})
        self.assertEqual([r["invoice_id"] for r in result["rows"]], ["INV-100003"])
        self.assertFalse(result["rows"][0]["queued"])

    def test_scope_and_numeric_sort(self):
        result = server.ledger({"scope": ["overdue"], "sort": ["balance"], "direction": ["asc"]})
        self.assertEqual([r["balance"] for r in result["rows"]], [40, 100])
        paid = server.ledger({"scope": ["paid"]})["rows"][0]
        self.assertEqual(paid["days"], 0)

    def test_empty_and_invalid_parameters(self):
        result = server.ledger({"q": ["missing"], "page": ["nonsense"], "sort": ["__invalid__"]})
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["page"], 1)

    def test_pagination_includes_every_invoice_once(self):
        original = self.world.invoices["INV-100001"]
        self.world.invoices = {f"INV-{i}": dict(original) for i in range(100000, 100123)}
        pages = [server.ledger({"page": [str(i)], "sort": ["invoice_id"]}) for i in (1, 2, 3)]
        ids = [r["invoice_id"] for p in pages for r in p["rows"]]
        self.assertEqual(len(ids), 123)
        self.assertEqual(len(set(ids)), 123)
        self.assertEqual(server.ledger({"page": ["99999"]})["page"], 3)


if __name__ == "__main__":
    unittest.main()
