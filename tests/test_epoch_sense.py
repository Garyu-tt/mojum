import pytest
from mojum.epoch_sense import (EPOCH_LENGTH, EpochProprioception, split_tick,
                               observed_at_ns, MAX_ABSOLUTE_TICK)
from mojum.local_cell_sense import DevelopmentError

REV = "a" * 64


def test_crosses_old_boundary_monotonically():
    prev = -1
    for t in (9998, 9999, 10000, 10050):
        s = EpochProprioception.at(t, REV, 1, 2, False)
        assert observed_at_ns(t) > prev
        prev = observed_at_ns(t)
        assert s.epoch * EPOCH_LENGTH + s.local_tick == t
    assert split_tick(9999) == (1, 0)
    assert split_tick(10050) == (1, 51)


def test_inconsistent_or_invalid_rejected():
    for bad in (-1, True, 1.0, MAX_ABSOLUTE_TICK + 1):
        with pytest.raises(DevelopmentError):
            split_tick(bad)
    with pytest.raises(DevelopmentError):
        EpochProprioception(10000, 0, 10000, REV, 1, 2, False)
    with pytest.raises(DevelopmentError):
        EpochProprioception.at(5, "x", 1, 2, False)


def test_public_schema_distinct():
    p = EpochProprioception.at(10000, REV, 1, 2, True).public()
    assert p["schema"].endswith("/4") and p["epoch"] == 1
