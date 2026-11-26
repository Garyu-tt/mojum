from __future__ import annotations

import gc
import sqlite3
import unittest
import warnings

from tools import spartan_budget
from tools.spartan_budgeted_call import (
    BudgetedCallError,
    dispatch,
)


class BudgetedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        # Match the row contract configured by spartan_budget.connect().
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        spartan_budget.schema(self.db)
        spartan_budget.configure(self.db, token_limit=1000, micro_usd_limit=50000)

    def call(self, response=None, receipts=None):
        calls = []

        def invoke():
            calls.append("called")
            return response if response is not None else {"private": "model output"}

        def extract(value):
            return receipts if receipts is not None else {
                "actual_tokens": 12,
                "actual_micro_usd": 340,
            }

        return calls, invoke, extract

    def test_reserves_before_invocation_and_settles_exact_usage(self):
        calls, invoke, extract = self.call()
        result = dispatch(
            self.db, request_id="request-1", worker=1,
            reserve_tokens=100, reserve_micro_usd=1000,
            invoke=invoke, receipt_extractor=extract,
        )
        self.assertEqual(calls, ["called"])
        self.assertTrue(result.dispatched)
        self.assertTrue(result.settled)
        self.assertEqual((result.actual_tokens, result.actual_micro_usd), (12, 340))
        self.assertEqual(result.response, {"private": "model output"})
        status = spartan_budget.status(self.db)
        self.assertEqual(status["debited_tokens"], 100)
        self.assertEqual(status["reported_tokens"], 12)
        self.assertEqual(status["unsettled"], 0)

    def test_duplicate_id_never_invokes_provider_again(self):
        calls, invoke, extract = self.call()
        kwargs = dict(
            request_id="request-dup", worker=2,
            reserve_tokens=100, reserve_micro_usd=1000,
            invoke=invoke, receipt_extractor=extract,
        )
        first = dispatch(self.db, **kwargs)
        second = dispatch(self.db, **kwargs)
        self.assertTrue(first.dispatched)
        self.assertFalse(second.dispatched)
        self.assertFalse(second.settled)
        self.assertEqual(second.state, "settled")
        self.assertEqual(calls, ["called"])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 1)

    def test_exhausted_budget_denies_before_provider_invocation(self):
        calls, invoke, extract = self.call()
        with self.assertRaises(spartan_budget.BudgetError):
            dispatch(
                self.db, request_id="too-large", worker=1,
                reserve_tokens=1001, reserve_micro_usd=1,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, [])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

    def test_provider_exception_keeps_full_reservation_debited(self):
        calls = []

        def invoke():
            calls.append("called")
            raise TimeoutError("ambiguous delivery")

        with self.assertRaises(TimeoutError):
            dispatch(
                self.db, request_id="provider-timeout", worker=3,
                reserve_tokens=80, reserve_micro_usd=800,
                invoke=invoke, receipt_extractor=lambda _: {},
            )
        self.assertEqual(calls, ["called"])
        status = spartan_budget.status(self.db)
        self.assertEqual(status["debited_tokens"], 80)
        self.assertEqual(status["unsettled"], 1)

    def test_receipt_extractor_exception_keeps_full_reservation(self):
        calls, invoke, _ = self.call()
        with self.assertRaisesRegex(RuntimeError, "bad receipt"):
            dispatch(
                self.db, request_id="bad-extractor", worker=4,
                reserve_tokens=80, reserve_micro_usd=800,
                invoke=invoke,
                receipt_extractor=lambda _: (_ for _ in ()).throw(RuntimeError("bad receipt")),
            )
        self.assertEqual(calls, ["called"])
        self.assertEqual(spartan_budget.status(self.db)["unsettled"], 1)

    def test_invalid_receipt_shape_fails_closed(self):
        calls, invoke, extract = self.call(receipts={"actual_tokens": 1})
        with self.assertRaises(BudgetedCallError):
            dispatch(
                self.db, request_id="bad-shape", worker=5,
                reserve_tokens=80, reserve_micro_usd=800,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, ["called"])
        self.assertEqual(spartan_budget.status(self.db)["unsettled"], 1)

    def test_bool_is_not_accepted_as_receipt_integer(self):
        calls, invoke, extract = self.call(
            receipts={"actual_tokens": True, "actual_micro_usd": 1}
        )
        with self.assertRaises(BudgetedCallError):
            dispatch(
                self.db, request_id="bool-receipt", worker=6,
                reserve_tokens=80, reserve_micro_usd=800,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, ["called"])
        self.assertEqual(spartan_budget.status(self.db)["unsettled"], 1)

    def test_provider_usage_overrun_freezes_ledger(self):
        calls, invoke, extract = self.call(
            receipts={"actual_tokens": 101, "actual_micro_usd": 500}
        )
        with self.assertRaises(spartan_budget.BudgetError):
            dispatch(
                self.db, request_id="overrun", worker=7,
                reserve_tokens=100, reserve_micro_usd=500,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, ["called"])
        self.assertTrue(spartan_budget.status(self.db)["frozen"])
        with self.assertRaises(spartan_budget.BudgetError):
            spartan_budget.reserve(self.db, "after-freeze", 8, 1, 0)

    def test_contradictory_duplicate_reservation_is_rejected(self):
        calls, invoke, extract = self.call()
        dispatch(
            self.db, request_id="conflict", worker=9,
            reserve_tokens=100, reserve_micro_usd=1000,
            invoke=invoke, receipt_extractor=extract,
        )
        with self.assertRaises(spartan_budget.BudgetError):
            dispatch(
                self.db, request_id="conflict", worker=9,
                reserve_tokens=99, reserve_micro_usd=1000,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, ["called"])

    def test_raw_tuple_row_connection_rejected_before_provider_call(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(db.close)
        calls = []
        with self.assertRaisesRegex(TypeError, "sqlite3.Row"):
            dispatch(
                db, request_id="wrong-row-factory", worker=1,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=lambda: calls.append("called"),
                receipt_extractor=lambda _: {"actual_tokens": 0, "actual_micro_usd": 0},
            )
        self.assertEqual(calls, [])

    def test_invalid_callbacks_are_rejected_before_reservation(self):
        with self.assertRaises(TypeError):
            dispatch(
                self.db, request_id="no-callback", worker=10,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=None, receipt_extractor=lambda _: {},
            )
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

    def test_async_invoke_is_rejected_before_reservation(self):
        calls = []

        async def invoke():
            calls.append("called")
            return {"content": "not dispatched"}

        def extract(_):
            return {"actual_tokens": 1, "actual_micro_usd": 1}

        with self.assertRaisesRegex(TypeError, "synchronous"):
            dispatch(
                self.db, request_id="async-invoke", worker=13,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, [])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

    def test_async_receipt_extractor_is_rejected_before_reservation(self):
        calls = []

        def invoke():
            calls.append("called")
            return {"content": "not dispatched"}

        async def extract(_):
            return {"actual_tokens": 1, "actual_micro_usd": 1}

        with self.assertRaisesRegex(TypeError, "synchronous"):
            dispatch(
                self.db, request_id="async-extractor", worker=14,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, [])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

    def test_sync_callbacks_returning_coroutines_are_closed(self):
        calls = []

        async def provider_coroutine():
            calls.append("provider-coroutine-body")
            return {"content": "not dispatched"}

        async def receipt_coroutine(_):
            calls.append("receipt-coroutine-body")
            return {"actual_tokens": 1, "actual_micro_usd": 1}

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)

            with self.assertRaisesRegex(BudgetedCallError, "provider callback"):
                dispatch(
                    self.db, request_id="returned-provider-coroutine", worker=17,
                    reserve_tokens=10, reserve_micro_usd=100,
                    invoke=lambda: provider_coroutine(),
                    receipt_extractor=lambda _: {"actual_tokens": 1, "actual_micro_usd": 1},
                )
            gc.collect()
            self.assertEqual(calls, [])
            self.assertEqual(spartan_budget.status(self.db)["unsettled"], 1)

            with self.assertRaisesRegex(BudgetedCallError, "receipt extractor"):
                dispatch(
                    self.db, request_id="returned-receipt-coroutine", worker=18,
                    reserve_tokens=10, reserve_micro_usd=100,
                    invoke=lambda: calls.append("provider-called"),
                    receipt_extractor=lambda value: receipt_coroutine(value),
                )
            gc.collect()
            self.assertEqual(calls, ["provider-called"])
            self.assertEqual(spartan_budget.status(self.db)["unsettled"], 2)

        self.assertFalse(
            any("was never awaited" in str(item.message) for item in caught),
            "rejected coroutine results must be closed without RuntimeWarning",
        )

    def test_async_callable_objects_are_rejected_before_reservation(self):
        calls = []

        class AsyncInvoker:
            async def __call__(self):
                calls.append("provider")
                return {"content": "must not run"}

        class AsyncExtractor:
            async def __call__(self, _):
                calls.append("extractor")
                return {"actual_tokens": 1, "actual_micro_usd": 1}

        with self.assertRaisesRegex(TypeError, "synchronous"):
            dispatch(
                self.db, request_id="async-callable-invoke", worker=15,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=AsyncInvoker(),
                receipt_extractor=lambda _: {"actual_tokens": 1, "actual_micro_usd": 1},
            )
        self.assertEqual(calls, [])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

        with self.assertRaisesRegex(TypeError, "synchronous"):
            dispatch(
                self.db, request_id="async-callable-extractor", worker=16,
                reserve_tokens=10, reserve_micro_usd=100,
                invoke=lambda: calls.append("provider"),
                receipt_extractor=AsyncExtractor(),
            )
        self.assertEqual(calls, [])
        self.assertEqual(spartan_budget.status(self.db)["requests"], 0)

    def test_response_is_not_written_to_budget_tables(self):
        marker = "NEVER-PERSIST-MODEL-OUTPUT-9e31"
        calls, invoke, extract = self.call(response={"content": marker})
        dispatch(
            self.db, request_id="redaction", worker=11,
            reserve_tokens=100, reserve_micro_usd=1000,
            invoke=invoke, receipt_extractor=extract,
        )
        rows = self.db.execute("SELECT * FROM requests").fetchall()
        self.assertNotIn(marker, repr([tuple(row) for row in rows]))

    def test_non_native_receipt_key_cannot_impersonate_usage_field(self):
        class ForgedKey(str):
            def __hash__(self):
                return hash("actual_tokens")

            def __eq__(self, other):
                return other == "actual_tokens"

        receipt = {
            ForgedKey("actual_tokens"): 12,
            "actual_micro_usd": 340,
        }
        calls, invoke, extract = self.call(receipts=receipt)
        with self.assertRaises(BudgetedCallError):
            dispatch(
                self.db, request_id="forged-key", worker=12,
                reserve_tokens=80, reserve_micro_usd=800,
                invoke=invoke, receipt_extractor=extract,
            )
        self.assertEqual(calls, ["called"])
        self.assertEqual(spartan_budget.status(self.db)["unsettled"], 1)


if __name__ == "__main__":
    unittest.main()
