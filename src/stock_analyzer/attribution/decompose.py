"""Market/sector/residual return decomposition via a jointly fitted regression.

docs/01_PRICE_ACTION_FRAMEWORK.md §2 step 1: "Strip the market.
r_stock = alpha + beta_mkt * r_mkt + beta_sector * r_sector + epsilon. Most
single-stock moves are market and sector. Only epsilon needs explaining."

Fits ONE joint OLS regression (not two separate univariate fits summed) when
a sector series is available: market and sector index returns are
correlated (e.g. corr(S&P 500, XLK) is high), so fitting each separately
against the stock and adding the products double-counts the shared market
component. Found in review: the original univariate-then-sum version
materially misattributed real moves for exactly this reason.

Betas and alpha are fitted via OLS on trailing daily returns strictly at or
before `as_of` (CLAUDE.md §3.1 rule 12: no look-ahead — every series is
explicitly truncated before fitting) — never read from a data provider's
opaque precomputed beta field, which valuation/dcf.py uses for a different,
forward-looking purpose.

The "unexplained share" is deliberately NOT |residual| / |total return|:
that ratio is unbounded, exceeds 100% whenever the components partly cancel
against a small total return, and is undefined when the total return is
exactly zero — confirmed in review to do exactly that on real fixture data.
Instead it is |residual| / (sum of the absolute value of every component,
residual included): always well-defined, always in [0, 1], and it answers a
more useful question — of all the identified forces at play, how much came
from the unexplained residual versus the parts the model can name.

Style-factor attribution (§2 step 2: regress the residual on value/momentum/
size/quality/low-vol factor RETURNS) is deliberately NOT implemented:
building those returns needs a broad cross-sectional universe to construct
long-short factor portfolios, which this project's pilot universe cannot
provide (config/attribution.yaml). Reported as unavailable, never
approximated with a single-stock proxy that would misrepresent what a style
factor return actually is.

Known, disclosed limitation: the market/sector indices used are not
adjusted to exclude the stock itself. For a large index constituent (e.g.
ASML is a major weight in the AEX), part of "the index's move" is
mechanically the stock's own move reflected back, inflating the fitted beta
and understating the true residual. No free, self-excluding index series
exists for this project's markets — treat beta/R^2 skeptically for
large-weight names rather than pretending the endogeneity isn't there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import statsmodels.api as sm

from ..data.base import PricePoint
from ..data.priceseries import daily_returns, nearest_point


class InsufficientDataError(Exception):
    """Not enough paired return observations to fit a trustworthy regression."""


def _truncate_to_as_of(points: list[PricePoint], as_of: date) -> list[PricePoint]:
    """CLAUDE.md §3.1 rule 12: a regression must never see data after as_of."""
    return [p for p in points if p.as_of <= as_of]


@dataclass(frozen=True)
class RegressionFit:
    alpha: float  # per-trading-day intercept
    beta_market: float
    beta_sector: float | None
    r_squared: float
    n_observations: int


def fit_regression(
    stock_points: list[PricePoint],
    market_points: list[PricePoint],
    sector_points: list[PricePoint] | None,
    as_of: date,
    min_observations: int,
) -> RegressionFit:
    """OLS over dates present in every series being used, paired by date (not
    index position — series can sit on different trading calendars)."""
    stock_points = _truncate_to_as_of(stock_points, as_of)
    market_points = _truncate_to_as_of(market_points, as_of)
    stock_returns = daily_returns(stock_points)
    market_returns = daily_returns(market_points)

    sector_returns: dict[date, float] | None = None
    if sector_points is not None:
        sector_points = _truncate_to_as_of(sector_points, as_of)
        sector_returns = daily_returns(sector_points)
        common_dates = sorted(set(stock_returns) & set(market_returns) & set(sector_returns))
    else:
        common_dates = sorted(set(stock_returns) & set(market_returns))

    if len(common_dates) < min_observations:
        raise InsufficientDataError(
            f"only {len(common_dates)} paired daily observations, need >= {min_observations}"
        )

    y = np.array([stock_returns[d] for d in common_dates])
    if sector_returns is not None:
        x = np.column_stack(
            [
                [market_returns[d] for d in common_dates],
                [sector_returns[d] for d in common_dates],
            ]
        )
    else:
        x = np.array([market_returns[d] for d in common_dates]).reshape(-1, 1)

    model = sm.OLS(y, sm.add_constant(x)).fit()
    params = np.asarray(model.params)
    return RegressionFit(
        alpha=float(params[0]),
        beta_market=float(params[1]),
        beta_sector=float(params[2]) if sector_returns is not None else None,
        r_squared=float(model.rsquared),
        n_observations=len(common_dates),
    )


def window_return(points: list[PricePoint], as_of: date, window_days: int) -> float | None:
    """Simple return from ~window_days ago to as_of. None if either endpoint
    can't be matched within tolerance — never impute a window return from a
    mismatched or missing point."""
    end = nearest_point(points, as_of, 0)
    start = nearest_point(points, as_of, window_days)
    if end is None or start is None or start.close == 0:
        return None
    return (end.close / start.close) - 1.0


def _return_observations_in_window(points: list[PricePoint], as_of: date, window_days: int) -> int:
    """How many daily-return observations fall in (as_of - window_days, as_of]
    — used to scale the per-day alpha intercept into a window-level figure."""
    window_start = as_of - timedelta(days=window_days)
    returns = daily_returns(_truncate_to_as_of(points, as_of))
    return sum(1 for d in returns if window_start < d <= as_of)


@dataclass(frozen=True)
class AttributionResult:
    ticker: str
    as_of: date
    window_days: int
    stock_return: float

    alpha_component: float
    market_beta: float
    market_return: float
    market_component: float
    r_squared: float
    n_observations: int

    sector_available: bool
    sector_beta: float | None
    sector_return: float | None
    sector_component: float | None
    sector_unavailable_reason: str | None

    style_available: bool  # always False — see module docstring

    residual: float
    unexplained_share: float  # bounded [0, 1] by construction — see module docstring


def decompose(
    ticker: str,
    as_of: date,
    window_days: int,
    stock_points: list[PricePoint],
    market_points: list[PricePoint],
    min_observations: int,
    sector_points: list[PricePoint] | None,
    sector_unavailable_reason: str | None,
) -> AttributionResult:
    stock_ret = window_return(stock_points, as_of, window_days)
    if stock_ret is None:
        raise InsufficientDataError(f"{ticker}: no window return available as of {as_of}")
    market_ret = window_return(market_points, as_of, window_days)
    if market_ret is None:
        raise InsufficientDataError(f"{ticker}: no market index window return as of {as_of}")

    sector_ret: float | None = None
    effective_sector_reason = sector_unavailable_reason
    if sector_points is not None:
        sector_ret = window_return(sector_points, as_of, window_days)
        if sector_ret is None:
            effective_sector_reason = "sector index has no window return covering this period"

    use_sector = sector_ret is not None
    try:
        fit = fit_regression(
            stock_points,
            market_points,
            sector_points if use_sector else None,
            as_of,
            min_observations,
        )
    except InsufficientDataError as exc:
        if use_sector:
            # A sector data problem must degrade to market-only, never crash
            # the whole attribution — found in review as a real failure mode.
            effective_sector_reason = f"sector regression unusable: {exc}"
            use_sector = False
            sector_ret = None
            fit = fit_regression(stock_points, market_points, None, as_of, min_observations)
        else:
            raise

    n_days = _return_observations_in_window(stock_points, as_of, window_days)
    alpha_component = fit.alpha * n_days
    market_component = fit.beta_market * market_ret

    sector_component: float | None = None
    if use_sector and fit.beta_sector is not None and sector_ret is not None:
        sector_component = fit.beta_sector * sector_ret
    else:
        use_sector = False

    explained_terms = [alpha_component, market_component]
    if sector_component is not None:
        explained_terms.append(sector_component)
    residual = stock_ret - sum(explained_terms)

    all_terms = [*explained_terms, residual]
    denominator = sum(abs(t) for t in all_terms)
    unexplained_share = abs(residual) / denominator if denominator > 0 else 0.0

    return AttributionResult(
        ticker=ticker,
        as_of=as_of,
        window_days=window_days,
        stock_return=stock_ret,
        alpha_component=alpha_component,
        market_beta=fit.beta_market,
        market_return=market_ret,
        market_component=market_component,
        r_squared=fit.r_squared,
        n_observations=fit.n_observations,
        sector_available=use_sector,
        sector_beta=fit.beta_sector if use_sector else None,
        sector_return=sector_ret if use_sector else None,
        sector_component=sector_component,
        sector_unavailable_reason=(
            None if use_sector else (effective_sector_reason or "sector data unavailable")
        ),
        style_available=False,
        residual=residual,
        unexplained_share=unexplained_share,
    )
