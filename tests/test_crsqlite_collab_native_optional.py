"""True upstream v0.16.3 two-peer test, conditional on a trusted local binary.

Unlike the hermetic query-recorder tests, this test exercises CRR merge logic.
It intentionally never downloads or installs native code.
"""
import os
import unittest

from mojum.crsqlite_collab import open_scratchpad


@unittest.skipUnless(os.environ.get('MOJUM_CRSQLITE_EXTENSION') and
                     os.environ.get('MOJUM_CRSQLITE_SHA256'),
                     'trusted CR-SQLite v0.16.3 extension + SHA256 not provided')
class OptionalNativeTwoPeerTests(unittest.TestCase):
    def test_partition_rejoin_and_idempotent_replay(self):
        def make():
            return open_scratchpad(
                enabled=True, classification='PUBLIC_SYNTHETIC',
                extension_path=os.environ['MOJUM_CRSQLITE_EXTENSION'],
                expected_sha256=os.environ['MOJUM_CRSQLITE_SHA256'])

        with make() as peer_a, make() as peer_b:
            # Separate local writes model a partition; neither peer can see
            # the other's state until explicit packet exchange.
            peer_a.put('note_a', 'blue')
            peer_b.put('note_b', 'green')
            self.assertEqual(peer_a.notes(), (('note_a', 'blue'),))
            self.assertEqual(peer_b.notes(), (('note_b', 'green'),))
            a_packet, b_packet = peer_a.export(), peer_b.export()
            peer_a.apply(b_packet, trusted_local_fixture=True)
            peer_b.apply(a_packet, trusted_local_fixture=True)
            expected = (('note_a', 'blue'), ('note_b', 'green'))
            self.assertEqual(peer_a.notes(), expected)
            self.assertEqual(peer_b.notes(), expected)
            peer_a.apply(b_packet, trusted_local_fixture=True)
            peer_b.apply(a_packet, trusted_local_fixture=True)
            self.assertEqual(peer_a.notes(), expected)
            self.assertEqual(peer_b.notes(), expected)


if __name__ == '__main__':
    unittest.main()
