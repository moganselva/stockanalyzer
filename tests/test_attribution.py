"""Attribution: market/sector/residual decomposition and event-tape matching.
docs/01_PRICE_ACTION_FRAMEWORK.md §2.

MILESTONES.md M4 goal: `analyze why` must report the unexplained share as an
explicit headline number, and a run that explains 100% of every move is a
bug — verified here on real fixtures across all four pilot tickers, each
regressed against ITS OWN region's index (not one index for all four), with
bounded [0,1] assertions rather than just "not exactly zero".
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from stock_analyzer.attribution.channel import Channel, classify_channel
from stock_analyzer.attribution.decompose import (
    AttributionResult,
    InsufficientDataError,
    decompose,
    fit_regression,
    window_return,
)
from stock_analyzer.attribution.events import (
    MIN_CONFIRMING_SURPRISE_PCT,
    Confidence,
    EventMatch,
    earnings_events_in_window,
    label_confidence,
)
from stock_analyzer.data.base import EarningsEvent, PricePoint
from stock_analyzer.data.providers.yfinance_provider import YFinanceProvider

FIXTURES_DIR = Path("tests/fixtures")
TICKER_TO_REGION_INDEX = {
    "AAPL": "^GSPC",
    "7203.T": "^N225",
    "ASML.AS": "^AEX",
    "1299.HK": "^HSI",
}


def _synthetic_series(n: int, start: date, daily_rets: list[float]) -> list[PricePoint]:
    assert len(daily_rets) == n - 1
    points = [PricePoint(as_of=start, close=100.0)]
    for i, r in enumerate(daily_rets):
        prev = points[-1].close
        points.append(PricePoint(as_of=start + timedelta(days=i + 1), close=prev * (1 + r)))
    return points


# --- fit_regression ---


def test_fit_regression_recovers_known_market_only_relationship() -> None:
    start = date(2025, 1, 1)
    market_rets = [0.001 * ((i % 7) - 3) for i in range(120)]
    true_beta = 1.5
    stock_rets = [true_beta * r for r in market_rets]
    stock_points = _synthetic_series(121, start, stock_rets)
    market_points = _synthetic_series(121, start, market_rets)

    fit = fit_regression(stock_points, market_points, None, start + timedelta(days=120), 60)
    assert fit.beta_market == pytest.approx(true_beta, abs=1e-6)
    assert fit.beta_sector is None
    assert fit.alpha == pytest.approx(0.0, abs=1e-9)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-6)


def test_fit_regression_joint_fit_does_not_double_count_correlated_regressors() -> None:
    """The core M4 review finding: fitting market and sector separately and
    summing double-counts their shared component. A joint fit must not."""
    start = date(2025, 1, 1)
    n = 150
    market_rets = [0.001 * ((i % 7) - 3) for i in range(n)]
    # sector is highly correlated with market plus a bit of its own signal
    sector_rets = [0.9 * m + 0.001 * ((i % 5) - 2) for i, m in enumerate(market_rets)]
    # true relationship: stock = 1.0*market + 0.5*sector, no alpha
    stock_rets = [1.0 * m + 0.5 * s for m, s in zip(market_rets, sector_rets, strict=True)]

    stock_points = _synthetic_series(n + 1, start, stock_rets)
    market_points = _synthetic_series(n + 1, start, market_rets)
    sector_points = _synthetic_series(n + 1, start, sector_rets)
    as_of = start + timedelta(days=n)

    fit = fit_regression(stock_points, market_points, sector_points, as_of, 60)
    assert fit.beta_market == pytest.approx(1.0, abs=1e-6)
    assert fit.beta_sector == pytest.approx(0.5, abs=1e-6)


def test_fit_regression_alpha_recovers_known_drift() -> None:
    start = date(2025, 1, 1)
    market_rets = [0.001 * ((i % 7) - 3) for i in range(100)]
    true_alpha = 0.0005
    stock_rets = [true_alpha + 1.0 * r for r in market_rets]
    stock_points = _synthetic_series(101, start, stock_rets)
    market_points = _synthetic_series(101, start, market_rets)

    fit = fit_regression(stock_points, market_points, None, start + timedelta(days=100), 60)
    assert fit.alpha == pytest.approx(true_alpha, abs=1e-6)


def test_fit_regression_raises_with_too_few_observations() -> None:
    start = date(2025, 1, 1)
    stock_points = _synthetic_series(11, start, [0.01] * 10)
    market_points = _synthetic_series(11, start, [0.01] * 10)
    with pytest.raises(InsufficientDataError):
        fit_regression(stock_points, market_points, None, start + timedelta(days=10), 60)


def test_fit_regression_never_uses_data_after_as_of() -> None:
    """CLAUDE.md §3.1 rule 12: no look-ahead. A regression anchored on an
    earlier as_of must produce a different (and only earlier-informed) fit
    than one anchored on the full series — confirming truncation actually
    happens rather than being silently bypassed."""
    start = date(2025, 1, 1)
    n = 300
    # a relationship that changes partway through the series
    market_rets = [0.001 * ((i % 7) - 3) for i in range(n)]
    stock_rets = [(1.0 if i < 150 else 3.0) * r for i, r in enumerate(market_rets)]
    stock_points = _synthetic_series(n + 1, start, stock_rets)
    market_points = _synthetic_series(n + 1, start, market_rets)

    early_as_of = start + timedelta(days=140)  # before the regime change
    late_as_of = start + timedelta(days=n)

    early_fit = fit_regression(stock_points, market_points, None, early_as_of, 60)
    late_fit = fit_regression(stock_points, market_points, None, late_as_of, 60)
    assert early_fit.beta_market == pytest.approx(1.0, abs=0.05)
    assert late_fit.beta_market != pytest.approx(early_fit.beta_market, abs=0.1)


def test_fit_regression_pairs_by_date_not_position() -> None:
    start = date(2025, 1, 1)
    stock_rets = [0.01 + 0.001 * ((i % 7) - 3) for i in range(100)]
    market_rets = [0.02 + 0.002 * ((i % 5) - 2) for i in range(100)]
    stock_points = _synthetic_series(101, start, stock_rets)
    full_market_series = _synthetic_series(101, start, market_rets)
    market_points = [p for i, p in enumerate(full_market_series) if i % 5 != 0]
    fit = fit_regression(stock_points, market_points, None, start + timedelta(days=100), 10)
    assert fit.n_observations < 100


# --- window_return ---


def test_window_return_matches_hand_computed_value() -> None:
    start = date(2025, 1, 1)
    points = _synthetic_series(31, start, [0.0] * 29 + [0.10])
    ret = window_return(points, points[-1].as_of, window_days=30)
    assert ret == pytest.approx(0.10, abs=1e-6)


def test_window_return_none_when_endpoint_unreachable() -> None:
    start = date(2025, 1, 1)
    points = _synthetic_series(5, start, [0.01] * 4)
    ret = window_return(points, points[-1].as_of, window_days=365)
    assert ret is None


# --- decompose: real fixtures, each ticker against its own region's index ---


@pytest.mark.parametrize("ticker", list(TICKER_TO_REGION_INDEX))
def test_decompose_real_fixtures_unexplained_share_is_bounded(ticker: str) -> None:
    """The core M4 anti-fabrication check, done properly: unexplained_share
    must be a real, bounded [0,1] share, on every pilot ticker against its
    own market index — not an unbounded ratio that can exceed 100%."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    stock_history = yfin.get_price_history(ticker)
    market_history = yfin.get_price_history(TICKER_TO_REGION_INDEX[ticker])
    result = decompose(
        ticker=ticker,
        as_of=stock_history.as_of,
        window_days=30,
        stock_points=stock_history.value,
        market_points=market_history.value,
        min_observations=60,
        sector_points=None,
        sector_unavailable_reason="test — sector not exercised here",
    )
    assert result.residual != 0.0
    assert 0.0 <= result.unexplained_share <= 1.0


def test_decompose_never_explains_100_percent_across_all_four_tickers() -> None:
    """MILESTONES.md M4 goal, checked in aggregate: not every ticker should
    show a trivial (near-zero) unexplained share — a system that always
    explains everything is broken."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    shares = []
    for ticker, index in TICKER_TO_REGION_INDEX.items():
        stock_history = yfin.get_price_history(ticker)
        market_history = yfin.get_price_history(index)
        result = decompose(
            ticker=ticker,
            as_of=stock_history.as_of,
            window_days=30,
            stock_points=stock_history.value,
            market_points=market_history.value,
            min_observations=60,
            sector_points=None,
            sector_unavailable_reason="test",
        )
        shares.append(result.unexplained_share)
    assert all(s > 0.05 for s in shares), f"some ticker explained almost everything: {shares}"


def test_at_least_one_of_four_pilot_tickers_is_genuinely_unexplained() -> None:
    """1299.HK (AIA) has no earnings print within any 30d trailing window in
    the fixture data (its one recorded row is a future scheduled date), so
    it must resolve to Confidence.UNEXPLAINED, not a fabricated label."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    stock_history = yfin.get_price_history("1299.HK")
    market_history = yfin.get_price_history("^HSI")
    result = decompose(
        ticker="1299.HK",
        as_of=stock_history.as_of,
        window_days=30,
        stock_points=stock_history.value,
        market_points=market_history.value,
        min_observations=60,
        sector_points=None,
        sector_unavailable_reason="HK sector proxy not configured",
    )
    earnings = yfin.get_earnings_history("1299.HK")
    matches = earnings_events_in_window(earnings.value, result.as_of, 30)
    confidence, _ = label_confidence(result.residual, matches)
    assert confidence == Confidence.UNEXPLAINED


def test_sector_available_when_real_sector_data_provided() -> None:
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    stock_history = yfin.get_price_history("AAPL")
    market_history = yfin.get_price_history("^GSPC")
    sector_history = yfin.get_price_history("XLK")
    result = decompose(
        ticker="AAPL",
        as_of=stock_history.as_of,
        window_days=30,
        stock_points=stock_history.value,
        market_points=market_history.value,
        min_observations=60,
        sector_points=sector_history.value,
        sector_unavailable_reason=None,
    )
    assert result.sector_available is True
    assert result.sector_unavailable_reason is None
    assert result.sector_component is not None
    assert result.sector_beta is not None


def test_sector_insufficient_data_degrades_to_market_only_not_a_crash() -> None:
    """A sector regression that can't be fit must fall back to market-only
    with a clear reason, never raise and abort the whole attribution."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    stock_history = yfin.get_price_history("AAPL")
    market_history = yfin.get_price_history("^GSPC")
    as_of = stock_history.as_of
    # Two points, spanning the 30d window closely enough for window_return to
    # succeed, but with only ~1 paired daily return — nowhere near
    # min_observations=60, so the JOINT regression must fail while the
    # window return itself remains computable.
    sparse_sector_points = [
        PricePoint(as_of=as_of - timedelta(days=34), close=100.0),
        PricePoint(as_of=as_of, close=105.0),
    ]
    result = decompose(
        ticker="AAPL",
        as_of=as_of,
        window_days=30,
        stock_points=stock_history.value,
        market_points=market_history.value,
        min_observations=60,
        sector_points=sparse_sector_points,
        sector_unavailable_reason=None,
    )
    assert result.sector_available is False
    assert result.sector_unavailable_reason is not None
    assert "regression unusable" in result.sector_unavailable_reason


def test_style_is_always_reported_unavailable() -> None:
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    stock_history = yfin.get_price_history("AAPL")
    market_history = yfin.get_price_history("^GSPC")
    result = decompose(
        ticker="AAPL",
        as_of=stock_history.as_of,
        window_days=30,
        stock_points=stock_history.value,
        market_points=market_history.value,
        min_observations=60,
        sector_points=None,
        sector_unavailable_reason="not exercised here",
    )
    assert result.style_available is False


def test_get_earnings_history_as_of_is_not_a_future_scheduled_date() -> None:
    """Regression for the review finding: 1299.HK's only fixture row is an
    unreported, future-scheduled earnings date — as_of must not be dated
    after the data was actually fetched."""
    yfin = YFinanceProvider(offline_fixtures_dir=FIXTURES_DIR)
    earnings = yfin.get_earnings_history("1299.HK")
    unreported_future_date = date(2026, 8, 19)
    assert earnings.as_of != unreported_future_date


# --- events.py ---


def _earnings_event(days_ago: int, as_of: date, surprise: float | None = None) -> EarningsEvent:
    return EarningsEvent(
        as_of=as_of - timedelta(days=days_ago),
        eps_estimate=1.0,
        eps_reported=1.0 + (surprise or 0) / 100 if surprise is not None else None,
        surprise_pct=surprise,
    )


def test_earnings_events_in_window_excludes_events_outside_range() -> None:
    as_of = date(2026, 8, 14)
    events = [_earnings_event(10, as_of, 5.0), _earnings_event(45, as_of, 3.0)]
    matches = earnings_events_in_window(events, as_of, window_days=30)
    assert len(matches) == 1
    assert matches[0].days_before_as_of == 10


def test_label_confidence_confirmed_when_surprise_agrees_and_clears_magnitude_floor() -> None:
    as_of = date(2026, 8, 14)
    strong_surprise = _earnings_event(5, as_of, MIN_CONFIRMING_SURPRISE_PCT + 1)
    matches = [EventMatch(event=strong_surprise, days_before_as_of=5)]
    confidence, _ = label_confidence(residual=0.05, matches=matches)
    assert confidence == Confidence.CONFIRMED


def test_label_confidence_not_confirmed_when_surprise_too_small() -> None:
    """Regression for the review's exact counterexample: a token-sized
    surprise (e.g. +0.17%) must not 'Confirm' an unrelated large residual
    just because the sign happens to agree."""
    as_of = date(2026, 8, 14)
    matches = [EventMatch(event=_earnings_event(5, as_of, 0.17), days_before_as_of=5)]
    confidence, reason = label_confidence(residual=0.18, matches=matches)
    assert confidence == Confidence.PROBABLE
    assert "threshold" in reason


def test_label_confidence_probable_when_conflicting_signs_present() -> None:
    """Regression for the review's cherry-picking finding: a disagreeing
    print in the same window must not be silently dropped in favor of the
    agreeing one."""
    as_of = date(2026, 8, 14)
    matches = [
        EventMatch(event=_earnings_event(5, as_of, 10.0), days_before_as_of=5),
        EventMatch(event=_earnings_event(20, as_of, -8.0), days_before_as_of=20),
    ]
    confidence, reason = label_confidence(residual=0.05, matches=matches)
    assert confidence == Confidence.PROBABLE
    assert "mixed signal" in reason


def test_label_confidence_probable_when_surprise_disagrees_with_residual() -> None:
    as_of = date(2026, 8, 14)
    strong_surprise = _earnings_event(5, as_of, MIN_CONFIRMING_SURPRISE_PCT + 1)
    matches = [EventMatch(event=strong_surprise, days_before_as_of=5)]
    confidence, _ = label_confidence(residual=-0.05, matches=matches)
    assert confidence == Confidence.PROBABLE


def test_label_confidence_unexplained_with_no_matches() -> None:
    confidence, reason = label_confidence(residual=-0.05, matches=[])
    assert confidence == Confidence.UNEXPLAINED
    assert "no earnings event" in reason


def test_label_confidence_probable_when_no_reported_surprise_yet() -> None:
    as_of = date(2026, 8, 14)
    matches = [EventMatch(event=_earnings_event(5, as_of, None), days_before_as_of=5)]
    confidence, _ = label_confidence(residual=0.05, matches=matches)
    assert confidence == Confidence.PROBABLE


# --- channel.py ---


def _result_with_unexplained_share(share: float) -> AttributionResult:
    return AttributionResult(
        ticker="TEST",
        as_of=date(2026, 8, 14),
        window_days=30,
        stock_return=0.05,
        alpha_component=0.0,
        market_beta=1.0,
        market_return=0.05 * (1 - share),
        market_component=0.05 * (1 - share),
        r_squared=0.5,
        n_observations=100,
        sector_available=False,
        sector_beta=None,
        sector_return=None,
        sector_component=None,
        sector_unavailable_reason="n/a",
        style_available=False,
        residual=0.05 * share,
        unexplained_share=share,
    )


def test_classify_channel_e_when_confirmed_earnings_event() -> None:
    result = _result_with_unexplained_share(0.9)
    channel, _ = classify_channel(result, Confidence.CONFIRMED)
    assert channel == Channel.E


def test_classify_channel_m_when_beta_explains_most_of_move() -> None:
    result = _result_with_unexplained_share(0.2)
    channel, _ = classify_channel(result, Confidence.UNEXPLAINED)
    assert channel == Channel.M


def test_classify_channel_unclassified_otherwise() -> None:
    result = _result_with_unexplained_share(0.9)
    channel, _ = classify_channel(result, Confidence.UNEXPLAINED)
    assert channel == Channel.UNCLASSIFIED
