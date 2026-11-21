"""Synthetic fencing fixture: no networking, hardware, or real leases."""
import unittest
from mojum.fencing import Lease, FenceError, authorize


class FencingTests(unittest.TestCase):
    def test_append(self):
        self.assertEqual(authorize(Lease(1, 'a', 0), Lease(1, 'a', 1)).revision, 1)

    def test_competing_writer_rejected(self):
        with self.assertRaises(FenceError):
            authorize(Lease(1, 'a', 0), Lease(1, 'b', 1))

    def test_replay_and_revision_gap_rejected(self):
        for revision in (0, 2):
            with self.subTest(revision=revision), self.assertRaises(FenceError):
                authorize(Lease(1, 'a', 0), Lease(1, 'a', revision))

    def test_epoch_transition(self):
        self.assertEqual(authorize(Lease(1, 'a', 9), Lease(2, 'b', 0)).writer, 'b')

    def test_old_epoch_and_skip_rejected(self):
        for epoch in (1, 4):
            with self.subTest(epoch=epoch), self.assertRaises(FenceError):
                authorize(Lease(2, 'b', 0), Lease(epoch, 'a', 0))

    def test_epoch_transition_requires_zero_revision(self):
        with self.assertRaises(FenceError):
            authorize(Lease(1, 'a', 9), Lease(2, 'b', 10))

    def test_bad_inputs_fail_closed(self):
        for args in ((True, 'a', 0), (0, 'a', 0), (1, '', 0), (1, 'a', -1), (1, 'a', False)):
            with self.subTest(args=args), self.assertRaises(FenceError):
                Lease(*args)

    def test_requires_leases(self):
        with self.assertRaises(FenceError):
            authorize({}, Lease(1, 'a', 0))


    def test_canonical_writer_ids_accepted(self):
        for writer in ('a', 'node-1', 'node_2', 'node.3', 'node:4', 'A9',
                       'a.b-c_d:42', 'n' * 128):
            with self.subTest(writer=writer):
                self.assertEqual(Lease(1, writer, 0).writer, writer)

    def test_invalid_writer_ids_rejected(self):
        for writer in ('', ' ', ' node ', 'node ', 'node\x00two',
                       'node\nlog-forgery', 'n' * 129, '.', '../a', 'a/b',
                       'a..b', 'a--b', 'a@b', 'пиксель', 'a\tb', 'a\x7fb'):
            with self.subTest(writer=writer), self.assertRaises(FenceError):
                Lease(1, writer, 0)


if __name__ == '__main__':
    unittest.main()
