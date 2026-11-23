import unittest

from mojum.mesh_replication_lag import (
    CheckpointHead,
    ReplicationLagReport,
    SCHEMA,
    measure_replication_lag,
)


def head(*, stream="organism-1", epoch=2, sequence=10, at=100, digest="a"):
    return CheckpointHead(
        stream_id=stream,
        epoch=epoch,
        sequence=sequence,
        committed_at_ns=at,
        state_sha256=digest * 64,
    )


class ReplicationLagTests(unittest.TestCase):
    def measure(self, source=None, replica=None, **kwargs):
        return measure_replication_lag(
            source or head(sequence=12, at=200, digest="b"),
            replica or head(sequence=12, at=200, digest="b"),
            now_ns=kwargs.pop("now_ns", 300),
            **kwargs,
        )

    def test_equal_heads_are_current(self):
        report = self.measure()
        self.assertIsInstance(report, ReplicationLagReport)
        self.assertEqual(report.schema, SCHEMA)
        self.assertEqual(report.sequence_lag, 0)
        self.assertEqual(report.replica_age_ns, 100)
        self.assertEqual(report.source_age_ns, 100)
        self.assertEqual(report.status, "current")

    def test_lagging_by_sequence(self):
        report = self.measure(
            source=head(sequence=12, at=200, digest="b"),
            replica=head(sequence=9, at=180),
            max_sequence_lag=2,
        )
        self.assertEqual(report.sequence_lag, 3)
        self.assertEqual(report.status, "lagging")

    def test_sequence_threshold_is_inclusive(self):
        report = self.measure(
            source=head(sequence=12, at=200, digest="b"),
            replica=head(sequence=10, at=180),
            max_sequence_lag=2,
        )
        self.assertEqual(report.status, "current")

    def test_age_threshold_is_inclusive(self):
        report = self.measure(max_replica_age_ns=100)
        self.assertEqual(report.status, "current")

    def test_stale_takes_precedence_over_lagging(self):
        report = self.measure(
            source=head(sequence=12, at=200, digest="b"),
            replica=head(sequence=1, at=100),
            now_ns=300,
            max_sequence_lag=0,
            max_replica_age_ns=50,
        )
        self.assertEqual(report.status, "stale")

    def test_source_age_is_reported_separately(self):
        report = self.measure(now_ns=350)
        self.assertEqual(report.source_age_ns, 150)
        self.assertEqual(report.replica_age_ns, 150)

    def test_stream_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "stream mismatch"):
            self.measure(replica=head(stream="another-stream", sequence=12, at=200, digest="b"))

    def test_epoch_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "epoch mismatch"):
            self.measure(replica=head(epoch=3, sequence=12, at=200, digest="b"))

    def test_replica_ahead_rejected(self):
        with self.assertRaisesRegex(ValueError, "ahead of source"):
            self.measure(replica=head(sequence=13, at=200, digest="b"))

    def test_replica_timestamp_ahead_rejected(self):
        with self.assertRaisesRegex(ValueError, "timestamp is ahead"):
            self.measure(replica=head(sequence=11, at=201))

    def test_future_source_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.measure(source=head(sequence=12, at=301, digest="b"))

    def test_future_replica_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.measure(replica=head(sequence=11, at=301))

    def test_equal_sequence_divergent_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, "divergent"):
            self.measure(replica=head(sequence=12, at=200, digest="c"))

    def test_exact_head_type_required(self):
        class Child(CheckpointHead):
            pass
        with self.assertRaisesRegex(ValueError, "exact CheckpointHead"):
            self.measure(replica=Child("organism-1", 2, 12, 200, "b" * 64))

    def test_foreign_object_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact CheckpointHead"):
            self.measure(replica=object())

    def test_bool_not_accepted_as_epoch(self):
        bad = head(sequence=12, at=200, digest="b")
        object.__setattr__(bad, "epoch", True)
        with self.assertRaisesRegex(ValueError, "epoch"):
            self.measure(replica=bad)

    def test_bool_not_accepted_as_sequence(self):
        bad = head(sequence=12, at=200, digest="b")
        object.__setattr__(bad, "sequence", True)
        with self.assertRaisesRegex(ValueError, "sequence"):
            self.measure(replica=bad)

    def test_bool_not_accepted_as_timestamp(self):
        bad = head(sequence=12, at=200, digest="b")
        object.__setattr__(bad, "committed_at_ns", True)
        with self.assertRaisesRegex(ValueError, "commit time"):
            self.measure(replica=bad)

    def test_invalid_stream_identifier_rejected(self):
        for stream in ("", "../node", "a//b", "bad\nnode"):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                self.measure(replica=head(stream=stream, sequence=12, at=200, digest="b"))

    def test_invalid_digest_rejected(self):
        for digest in ("", "A" * 64, "g" * 64, "a" * 63):
            bad = head(sequence=12, at=200, digest="b")
            object.__setattr__(bad, "state_sha256", digest)
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.measure(replica=bad)

    def test_bool_now_and_thresholds_rejected(self):
        for kwargs in (
            {"now_ns": True},
            {"max_sequence_lag": True},
            {"max_replica_age_ns": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.measure(**kwargs)

    def test_negative_and_oversized_thresholds_rejected(self):
        for kwargs in (
            {"max_sequence_lag": -1},
            {"max_replica_age_ns": -1},
            {"max_sequence_lag": 1 << 63},
            {"max_replica_age_ns": 1 << 63},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.measure(**kwargs)

    def test_report_is_frozen(self):
        report = self.measure()
        with self.assertRaises((AttributeError, TypeError)):
            report.status = "promoted"

    def test_report_does_not_authorize_failover(self):
        report = self.measure()
        self.assertFalse(hasattr(report, "promote"))
        self.assertFalse(hasattr(report, "authorized"))

    def test_old_replica_is_current_when_thresholds_explicitly_allow_it(self):
        report = self.measure(
            source=head(sequence=12, at=200, digest="b"),
            replica=head(sequence=8, at=100),
            now_ns=300,
            max_sequence_lag=4,
            max_replica_age_ns=200,
        )
        self.assertEqual(report.status, "current")
        self.assertEqual(report.sequence_lag, 4)


if __name__ == "__main__":
    unittest.main()
