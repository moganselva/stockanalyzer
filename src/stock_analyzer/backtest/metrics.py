"""Performance metrics. docs/01_PRICE_ACTION_FRAMEWORK.md §8: "Deflate for
multiple testing. Every parameter you try inflates the observed Sharpe.
Apply the Deflated Sharpe Ratio (Bailey & López de Prado) and record the
number of configurations tested. A Sharpe of 1.5 found after 200 trials is
noise." Also: "Regime-split results. Report performance separately across
rate regimes, volatility regimes, and 2008/2020/2022-style stress windows."
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from scipy import stats

# Euler-Mascheroni constant, as used in Bailey & Lopez de Prado (2014),
# "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
# Overfitting, and Non-Normality" — distinct from math.e, which the same
# formula also uses.
_EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(returns: list[float], periods_per_year: float) -> float | None:
    """Annualised Sharpe ratio (mean/stdev * sqrt(periods_per_year)), using
    SAMPLE standard deviation — matching decision/sizing.py's realized_vol
    convention elsewhere in this project. None — never 0.0, which would
    look like "no edge" rather than "undefined" — when there are fewer than
    2 observations, or the sample stdev is exactly zero."""
    if len(returns) < 2:
        return None
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    return (statistics.mean(returns) / stdev) * math.sqrt(periods_per_year)


@dataclass(frozen=True)
class DeflatedSharpeResult:
    observed_sharpe_annualised: float
    period_sharpe: float  # non-annualised — the scale the DSR math itself operates in
    expected_max_sharpe_under_null: float  # per-period scale
    deflated_sharpe: float  # P(true Sharpe > 0), adjusted for selection over n_trials
    n_trials: int
    n_observations: int
    skewness: float
    kurtosis: float  # Pearson convention (normal distribution = 3.0)


def deflated_sharpe_ratio(
    returns: list[float], periods_per_year: float, n_trials: int
) -> DeflatedSharpeResult | None:
    """Corrects the observed (best-of-`n_trials`) Sharpe ratio for two
    effects an ordinary Sharpe ignores:

    1. Selection bias — picking the best of many tried configurations
       inflates the observed ratio purely by chance. The more trials, the
       higher the expected maximum Sharpe under a TRUE-skill-zero null.
    2. Non-normality — skewed, fat-tailed returns bias the Sharpe
       estimator's own sampling variance, which the classical Sharpe
       ratio's confidence interval ignores entirely.

    Returns None when there are too few observations (n < 4) to estimate
    skew/kurtosis/variance meaningfully, or when the estimation variance
    is non-positive (degenerate input) — never a fabricated confidence
    figure built on an unstable estimate."""
    n = len(returns)
    if n < 4:
        return None
    sr_annualised = sharpe_ratio(returns, periods_per_year)
    if sr_annualised is None:
        return None

    stdev = statistics.stdev(returns)
    period_sr = statistics.mean(returns) / stdev
    skewness = float(stats.skew(returns, bias=False))
    pearson_kurtosis = float(stats.kurtosis(returns, fisher=False, bias=False))

    variance_term = 1.0 - skewness * period_sr + ((pearson_kurtosis - 1.0) / 4.0) * period_sr**2
    if variance_term <= 0:
        return None
    sr_estimation_stdev = math.sqrt(variance_term / (n - 1))

    if n_trials <= 1:
        # No multiple-testing correction with a single trial — nothing was
        # selected out of a set, so there is nothing to deflate for.
        expected_max_sr = 0.0
    else:
        z_a = float(stats.norm.ppf(1.0 - 1.0 / n_trials))
        z_b = float(stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
        expected_max_sr = sr_estimation_stdev * (
            (1.0 - _EULER_MASCHERONI) * z_a + _EULER_MASCHERONI * z_b
        )

    deflated = float(stats.norm.cdf((period_sr - expected_max_sr) / sr_estimation_stdev))
    return DeflatedSharpeResult(
        observed_sharpe_annualised=sr_annualised,
        period_sharpe=period_sr,
        expected_max_sharpe_under_null=expected_max_sr,
        deflated_sharpe=deflated,
        n_trials=n_trials,
        n_observations=n,
        skewness=skewness,
        kurtosis=pearson_kurtosis,
    )


@dataclass(frozen=True)
class RegimeSplit:
    label: str
    n: int
    sharpe: float | None
    mean_return: float | None


def split_by_volatility_regime(
    returns: list[float], trailing_vols: list[float | None], periods_per_year: float
) -> dict[str, RegimeSplit]:
    """Splits a return series into HIGH-vol and LOW-vol buckets using each
    period's own trailing realized volatility (known at entry, so this
    split is itself point-in-time correct), divided at the SAMPLE median.

    docs/01_PRICE_ACTION_FRAMEWORK.md §8 calls for regime-split results
    across rate regimes, volatility regimes, and named 2008/2020/2022-style
    stress windows. This project's fetched price history does not reach
    back to any of those specific historical windows — disclosed here
    rather than faked. A volatility-regime split needs nothing but the
    series this project already has, so that is what this function
    provides; rate-regime and named-crisis splits are not implemented."""
    if len(returns) != len(trailing_vols):
        raise ValueError("returns and trailing_vols must be the same length, index-aligned")
    paired = [(r, v) for r, v in zip(returns, trailing_vols, strict=True) if v is not None]
    if not paired:
        return {
            "high_vol": RegimeSplit("high_vol", 0, None, None),
            "low_vol": RegimeSplit("low_vol", 0, None, None),
        }
    vols = sorted(v for _, v in paired)
    median_vol = vols[len(vols) // 2] if len(vols) % 2 == 1 else (
        vols[len(vols) // 2 - 1] + vols[len(vols) // 2]
    ) / 2.0

    high = [r for r, v in paired if v >= median_vol]
    low = [r for r, v in paired if v < median_vol]

    def _split(label: str, sample: list[float]) -> RegimeSplit:
        return RegimeSplit(
            label=label,
            n=len(sample),
            sharpe=sharpe_ratio(sample, periods_per_year=periods_per_year)
            if len(sample) >= 2
            else None,
            mean_return=statistics.mean(sample) if sample else None,
        )

    return {"high_vol": _split("high_vol", high), "low_vol": _split("low_vol", low)}
