"""backtest/metrics.py — Sharpe ratio and the Deflated Sharpe Ratio.
docs/01_PRICE_ACTION_FRAMEWORK.md §8: "Deflate for multiple testing. Every
parameter you try inflates the observed Sharpe. Apply the Deflated Sharpe
Ratio (Bailey & López de Prado) and record the number of configurations
tested. A Sharpe of 1.5 found after 200 trials is noise."
"""

from __future__ import annotations

import math
import random
import statistics

import pytest
from scipy import stats

from stock_analyzer.backtest.metrics import (
    deflated_sharpe_ratio,
    sharpe_ratio,
    split_by_volatility_regime,
)


def test_sharpe_ratio_hand_computed() -> None:
    returns = [0.01, 0.02, -0.01, 0.03, 0.00]
    mean = statistics.mean(returns)
    stdev = statistics.stdev(returns)
    expected = (mean / stdev) * math.sqrt(12)
    assert sharpe_ratio(returns, periods_per_year=12) == pytest.approx(expected)


def test_sharpe_ratio_none_with_fewer_than_two_observations() -> None:
    assert sharpe_ratio([], periods_per_year=12) is None
    assert sharpe_ratio([0.01], periods_per_year=12) is None


def test_sharpe_ratio_none_not_infinite_for_zero_stdev() -> None:
    """A constant return series has an undefined Sharpe ratio, not an
    infinite or fabricated one."""
    assert sharpe_ratio([0.01, 0.01, 0.01], periods_per_year=12) is None


def test_deflated_sharpe_none_with_fewer_than_four_observations() -> None:
    assert deflated_sharpe_ratio([0.01, 0.02, 0.03], periods_per_year=12, n_trials=5) is None


def test_deflated_sharpe_is_a_bounded_probability() -> None:
    random.seed(1)
    returns = [random.gauss(0.01, 0.05) for _ in range(50)]
    for n_trials in (1, 5, 50, 500):
        result = deflated_sharpe_ratio(returns, periods_per_year=12, n_trials=n_trials)
        assert result is not None
        assert 0.0 <= result.deflated_sharpe <= 1.0


def test_deflated_sharpe_attenuates_monotonically_as_trials_increase() -> None:
    """The framework's own stated intuition: "A Sharpe of 1.5 found after
    200 trials is noise." Holding the observed return series fixed, DSR
    must strictly decrease as the number of configurations tried grows —
    more trials means more chances the best one was luck, never fewer."""
    random.seed(7)
    returns = [random.gauss(0.015, 0.04) for _ in range(60)]
    trial_counts = [1, 2, 5, 10, 50, 200, 1000]
    results = [
        deflated_sharpe_ratio(returns, periods_per_year=12, n_trials=n) for n in trial_counts
    ]
    assert all(r is not None for r in results)
    dsr_values = [r.deflated_sharpe for r in results if r is not None]
    assert dsr_values == sorted(dsr_values, reverse=True), (
        f"DSR must strictly decrease as n_trials grows, got {dsr_values}"
    )
    # strictly, not just non-increasing
    assert len(set(dsr_values)) == len(dsr_values)


def test_single_trial_applies_no_multiple_testing_correction() -> None:
    """With n_trials=1 there is nothing to "select" the best of — the
    expected-maximum-Sharpe-under-the-null correction must be exactly 0,
    not merely small."""
    random.seed(3)
    returns = [random.gauss(0.01, 0.03) for _ in range(40)]
    result = deflated_sharpe_ratio(returns, periods_per_year=12, n_trials=1)
    assert result is not None
    assert result.expected_max_sharpe_under_null == 0.0


def test_deflated_sharpe_reduces_to_the_normal_case_formula_for_n_trials_one() -> None:
    """Internal-consistency check against the formula's own definition: for
    n_trials=1 (no selection-bias term) and returns with negligible skew/
    excess-kurtosis, the deflated Sharpe must equal Phi(period_sharpe *
    sqrt(n-1)) — the classical (non-deflated) probabilistic read of a
    Sharpe ratio's significance. This is not an external reference value;
    it is the formula's own n_trials=1 boundary condition, computed
    independently here and compared to what the implementation produces."""
    random.seed(11)
    returns = [random.gauss(0.0, 0.02) for _ in range(200)]  # large n -> skew/kurtosis near normal
    result = deflated_sharpe_ratio(returns, periods_per_year=12, n_trials=1)
    assert result is not None

    n = len(returns)
    period_sr = statistics.mean(returns) / statistics.stdev(returns)
    skewness = float(stats.skew(returns, bias=False))
    kurtosis = float(stats.kurtosis(returns, fisher=False, bias=False))
    variance_term = 1.0 - skewness * period_sr + ((kurtosis - 1.0) / 4.0) * period_sr**2
    sr_stdev = math.sqrt(variance_term / (n - 1))
    expected_dsr = float(stats.norm.cdf(period_sr / sr_stdev))
    assert result.deflated_sharpe == pytest.approx(expected_dsr, rel=1e-9)


def test_deflated_sharpe_higher_observed_sharpe_gives_higher_dsr_at_fixed_trials() -> None:
    random.seed(5)
    weak = [random.gauss(0.001, 0.05) for _ in range(60)]
    strong = [random.gauss(0.03, 0.05) for _ in range(60)]
    weak_result = deflated_sharpe_ratio(weak, periods_per_year=12, n_trials=20)
    strong_result = deflated_sharpe_ratio(strong, periods_per_year=12, n_trials=20)
    assert weak_result is not None
    assert strong_result is not None
    assert strong_result.deflated_sharpe > weak_result.deflated_sharpe


# ---------------------------------------------------------------------------
# split_by_volatility_regime
# ---------------------------------------------------------------------------


def test_regime_split_high_vol_bucket_gets_the_higher_vol_observations() -> None:
    returns = [0.01, 0.02, -0.01, 0.03, -0.02, 0.015]
    vols = [0.10, 0.30, 0.05, 0.40, 0.08, 0.25]  # median = (0.10+0.25)/2 = 0.175
    regimes = split_by_volatility_regime(returns, vols, periods_per_year=12)
    assert regimes["high_vol"].n + regimes["low_vol"].n == len(returns)
    assert regimes["high_vol"].n == 3  # 0.30, 0.40, 0.25 are >= median
    assert regimes["low_vol"].n == 3


def test_regime_split_skips_steps_with_unknown_vol() -> None:
    returns = [0.01, 0.02, 0.03]
    vols = [0.10, None, 0.20]
    regimes = split_by_volatility_regime(returns, vols, periods_per_year=12)
    assert regimes["high_vol"].n + regimes["low_vol"].n == 2


def test_regime_split_mismatched_lengths_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        split_by_volatility_regime([0.01, 0.02], [0.1], periods_per_year=12)


def test_regime_split_empty_input_reports_none_not_zero() -> None:
    regimes = split_by_volatility_regime([], [], periods_per_year=12)
    assert regimes["high_vol"].n == 0
    assert regimes["high_vol"].sharpe is None
    assert regimes["low_vol"].sharpe is None
