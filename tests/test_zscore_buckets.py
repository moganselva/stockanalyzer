"""CLAUDE.md §3.2 rule 8: sector/region-relative z-scores, winsorised at +/-3σ.

Pure-function tests with hand-built peer dicts — this is the arithmetic being
verified, not any provider's schema, so no fixtures are involved.

Algorithm reworked in the M2 quant-reviewer pass: winsorise the RAW peer values
before standardising (not the z-score after the fact — that still lets an
outlier distort the mean/stdev every other member is scored against), raised
the minimum bucket size from 2 to 5 (two peers always produces exactly +-1.00
regardless of how small the real gap is), and switched population stdev to
sample stdev (population stdev makes the +-3sigma bound mathematically
unreachable below 11 members, silently making winsorisation dead code).
"""

from __future__ import annotations

import pytest

from stock_analyzer.factors.registry import winsorized_zscore


def test_single_member_bucket_returns_none() -> None:
    """A bucket of one cannot be a cross-sectional signal — never fabricate a
    z-score of 0 to paper over having no real peers."""
    assert winsorized_zscore("AAPL", {"AAPL": 1.0}) is None


def test_below_minimum_bucket_size_returns_none() -> None:
    """Below min_peers (default 5), a sample stdev is too unstable to trust —
    found in review that the old min of 2 always produced exactly +-1.00 for
    any unequal pair, a maximally confident number carrying no real signal."""
    peers = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}  # only 4 members
    assert winsorized_zscore("a", peers) is None


def test_target_missing_from_peer_values_returns_none() -> None:
    peers = {"b": 1.0, "c": 2.0, "d": 3.0, "e": 4.0, "f": 5.0}
    assert winsorized_zscore("AAPL", peers) is None


def test_basic_zscore_matches_hand_computed_value() -> None:
    peers = {"a": 10.0, "b": 20.0, "c": 30.0, "d": 40.0, "e": 50.0}
    result_a = winsorized_zscore("a", peers)
    result_e = winsorized_zscore("e", peers)
    assert result_a is not None and result_e is not None
    assert result_a.z_score == pytest.approx(-1.2649110640673518)
    assert result_e.z_score == pytest.approx(1.2649110640673518)
    assert result_a.peer_count == 5
    assert result_a.winsorized is False


def test_zero_variance_bucket_returns_zero_not_division_error() -> None:
    peers = {"a": 5.0, "b": 5.0, "c": 5.0, "d": 5.0, "e": 5.0}
    result = winsorized_zscore("a", peers)
    assert result is not None
    assert result.z_score == 0.0
    assert result.winsorized is False


def test_extreme_outlier_is_winsorized_and_does_not_distort_other_members() -> None:
    peers = {f"p{i}": 0.0 for i in range(10)}
    peers["target"] = 1000.0
    outlier_result = winsorized_zscore("target", peers, sigma_clip=3.0)
    peer_result = winsorized_zscore("p0", peers, sigma_clip=3.0)
    assert outlier_result is not None and peer_result is not None

    assert outlier_result.z_score == pytest.approx(3.0151134457776365)
    assert outlier_result.winsorized is True
    assert outlier_result.peer_count == 11

    # The key property this algorithm exists for: an ordinary peer's score
    # stays modest even though one extreme outlier is in the same bucket —
    # the old (broken) order-of-operations let the outlier drag the raw mean
    # and stdev, which would have made every zero-valued peer's z close to 0
    # in a way that hid how extreme the outlier actually was.
    assert peer_result.z_score == pytest.approx(-0.30151134457776363)
    assert peer_result.winsorized is False
