"""Regression tests for transactional foreground-host signal installation.

All handlers are mocked: tests never send signals to the executing process.
"""
import signal
import threading
import unittest
from unittest.mock import patch

from mojum.portable_host import PortableHostError, termination_signals


@unittest.skipUnless(hasattr(signal, "SIGINT") and hasattr(signal, "SIGTERM"),
                     "host requires SIGINT and SIGTERM")
class TerminationSignalRollbackTests(unittest.TestCase):
    def test_failed_second_registration_restores_first_handler(self):
        original = {signal.SIGINT: object(), signal.SIGTERM: object()}
        calls = []

        def register(signum, handler):
            calls.append((signum, handler))
            if signum == signal.SIGTERM and callable(handler):
                raise ValueError("second handler could not be installed")

        with patch("mojum.portable_host.signal.getsignal",
                   side_effect=lambda signum: original[signum]), \
             patch("mojum.portable_host.signal.signal", side_effect=register):
            with self.assertRaisesRegex(
                    PortableHostError, "signal handlers require the main thread"):
                with termination_signals(threading.Event()):
                    self.fail("failed registration must not enter the body")

        self.assertEqual([signum for signum, _ in calls],
                         [signal.SIGINT, signal.SIGTERM, signal.SIGINT])
        self.assertTrue(callable(calls[0][1]))
        self.assertTrue(callable(calls[1][1]))
        self.assertIs(calls[-1][1], original[signal.SIGINT])

    def test_exception_in_body_restores_both_handlers_in_reverse_order(self):
        original = {signal.SIGINT: object(), signal.SIGTERM: object()}
        calls = []
        with patch("mojum.portable_host.signal.getsignal",
                   side_effect=lambda signum: original[signum]), \
             patch("mojum.portable_host.signal.signal",
                   side_effect=lambda signum, handler: calls.append((signum, handler))):
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with termination_signals(threading.Event()):
                    raise RuntimeError("fixture")

        self.assertEqual([signum for signum, _ in calls],
                         [signal.SIGINT, signal.SIGTERM,
                          signal.SIGTERM, signal.SIGINT])
        self.assertIs(calls[2][1], original[signal.SIGTERM])
        self.assertIs(calls[3][1], original[signal.SIGINT])

    def test_installed_handler_requests_stop_before_cleanup(self):
        saved_handlers = {}
        event = threading.Event()

        def capture(signum, handler):
            saved_handlers[signum] = handler

        with patch("mojum.portable_host.signal.getsignal", return_value=None), \
             patch("mojum.portable_host.signal.signal", side_effect=capture):
            with termination_signals(event):
                self.assertFalse(event.is_set())
                saved_handlers[signal.SIGTERM](signal.SIGTERM, None)
                self.assertTrue(event.is_set())

        self.assertIsNone(saved_handlers[signal.SIGINT])
        self.assertIsNone(saved_handlers[signal.SIGTERM])


if __name__ == "__main__":
    unittest.main()
