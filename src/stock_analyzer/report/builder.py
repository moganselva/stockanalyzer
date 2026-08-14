"""Assembles the self-contained JSON payload the reasoning layer reads.

CLAUDE.md §3.4 rule 13: "Claude receives a JSON payload, not raw data.
report/builder.py produces a complete, self-contained payload... Claude
reasons over this and nothing else." Rule 14: "Claude never does arithmetic
that matters." This module computes zero new judgement — it wires together
exactly the same functions the M1-M5 CLI commands (`fetch`, `factors`,
`value`, `why`, `decide`) already call, and serialises their results into
one dict. Every number in the payload already has a Python-computed home
before this module touches it.

Each section degrades independently: a provider failure in, say, attribution
must not prevent the valuation or decision sections from still being built.
CLAUDE.md §3.1 rule 4 applies here too — a section that could not be built is
reported as unavailable with a reason, never silently omitted or filled in.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..attribution.channel import classify_channel
from ..attribution.config import load_attribution_config
from ..attribution.decompose import InsufficientDataError, decompose
from ..attribution.events import earnings_events_in_window, label_confidence
from ..data.base import EarningsEvent, PricePoint, ProviderError, Value
from ..data.cache import Cache
from ..data.fetch import TickerRecord, fetch_ticker
from ..data.normalize import resolve_market
from ..data.priceseries import nearest_point
from ..data.providers.stooq_provider import StooqProvider
from ..data.providers.yfinance_provider import YFinanceProvider
from ..decision.config import DecisionRulesConfig, load_decision_rules
from ..decision.gates import GateContext, run_all_gates
from ..decision.scoring import (
    LONG_HORIZON_FACTOR_MAP,
    SHORT_HORIZON_FACTOR_MAP,
    compute_composite,
)
from ..decision.sizing import compute_sizing, realized_vol
from ..decision.tree import PositionContext, decide_action, timing_overlay
from ..factors.peers import build_peer_group
from ..factors.registry import (
    FactorContext,
    FactorDefinition,
    compute_factor,
    load_factor_config,
    validate_registry_matches_config,
    winsorized_zscore,
)
from ..factors.util import snapshot_field
from ..valuation.config import load_valuation_config
from ..valuation.dcf import DcfAssumptions, DcfInputError, cost_of_equity, dcf_value_per_share
from ..valuation.inputs import ValuationInputError, base_case_growth, gather_valuation_inputs
from ..valuation.multiples import compare_multiples
from ..valuation.reverse_dcf import NoImpliedGrowthInRange, sensitivity_table, solve_implied_growth
from ..valuation.scenarios import ScenarioProbabilityError, run_scenarios

DEFAULT_FIXTURES_DIR = Path("tests/fixtures")
DEFAULT_CACHE_PATH = Path("data/cache.db")
_SIZEABLE_ACTIONS = {"strong_buy", "buy", "hold", "hold_trim"}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def _jsonable(obj: Any) -> Any:
    """Recursively converts dataclasses / Value / date / StrEnum into plain
    JSON-compatible types. Never rounds or reformats a number — the payload
    must carry exactly the figure Python computed, not a display string."""
    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    if isinstance(obj, Value):
        return {
            "value": _jsonable(obj.value),
            "source": obj.source,
            "as_of": obj.as_of.isoformat(),
            "url": obj.url,
            "confidence": obj.confidence,
        }
    if isinstance(obj, date | datetime):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "value") and hasattr(obj, "name"):  # StrEnum
        return obj.value
    return obj


def _price_history_summary(
    points: list[PricePoint], as_of: date, source: str, currency: str | None
) -> dict[str, Any]:
    # Point-in-time guard (CLAUDE.md rule 12): every statistic below must be
    # computed only from points on or before `as_of`, the same invariant
    # data/priceseries.nearest_point already enforces for its own lookups —
    # found in review that period_high/period_low used the raw, unfiltered
    # `points` while latest_close (via nearest_point) was correctly filtered,
    # an inconsistency that is harmless only because this project's price
    # history today never extends past "now".
    eligible = [p for p in points if p.as_of <= as_of]
    if not eligible:
        return _unavailable("no price history on or before as_of")
    latest = nearest_point(eligible, as_of, 0)
    if latest is None:
        return _unavailable("no price point at or before as_of")
    summary: dict[str, Any] = {
        "available": True,
        "as_of": as_of.isoformat(),
        "source": source,
        "currency": currency,
        "latest_close": latest.close,
        "latest_close_as_of": latest.as_of.isoformat(),
        "n_points": len(eligible),
        "series_start": eligible[0].as_of.isoformat(),
        "series_end": eligible[-1].as_of.isoformat(),
        "period_high": max(p.close for p in eligible),
        "period_low": min(p.close for p in eligible),
        "price_basis_note": (
            "this series is split/dividend-ADJUSTED (yfinance auto_adjust=True) — "
            "do not compare latest_close directly against price.price_native or "
            "price.price_base, which are RAW quotes. latest_close_as_of may also "
            "differ from meta.as_of when the most recent trading day has no bar yet."
        ),
    }
    for label, days in (("1m", 30), ("3m", 91), ("6m", 182), ("12m", 365)):
        past = nearest_point(eligible, as_of, days)
        summary[f"return_{label}"] = (
            (latest.close / past.close) - 1.0 if past is not None and past.close != 0 else None
        )
    return summary


_EVENT_BETA_SESSIONS = (1, 20)  # docs/01_PRICE_ACTION_FRAMEWORK.md §4.1: 1-day and 20-day
_EVENT_BETA_MIN_SAMPLE = 12  # "...after its last 12 earnings beats"
_EVENT_MATCH_TOLERANCE_DAYS = 5


def _event_beta_stats(
    events: list[EarningsEvent], points: list[PricePoint], as_of: date
) -> dict[str, Any]:
    """Forward return following each REPORTED earnings print that falls
    inside the available price history, at each of `_EVENT_BETA_SESSIONS`
    trading sessions after the print.

    Point-in-time guard (CLAUDE.md rule 12): both `points` and `events` are
    filtered to `as_of` before use, so nothing here can read a bar or a
    print that would not have existed yet at the payload's anchor date.

    Every horizon is computed over the SAME qualifying-event set (an event
    only qualifies if price data exists out to the LONGEST requested
    horizon) — found in review that computing each horizon independently
    silently dropped the most recent print from the longer horizon as soon
    as fewer sessions than that horizon had elapsed, so two horizons could
    describe two different underlying events with no flag distinguishing
    "the pattern changed over time" from "the sample changed".

    Requested by the M6 equity-analyst dry run (finding G4): Part B §5
    requires magnitude anchored to "the stock's own historical event betas."
    Returns are RAW (not beta-adjusted against the market) and the provider
    carries no before/after-market timing flag for the print — both
    disclosed via notes rather than silently assumed away."""
    chronological = sorted((p for p in points if p.as_of <= as_of), key=lambda p: p.as_of)
    longest = max(_EVENT_BETA_SESSIONS)
    per_event: list[dict[str, Any]] = []
    for event in events:
        if event.eps_reported is None or event.surprise_pct is None or event.as_of > as_of:
            continue
        idx0 = next((i for i, p in enumerate(chronological) if p.as_of >= event.as_of), None)
        if idx0 is None:
            continue
        # An event date far outside the fetched price history (e.g. an
        # earnings print from years before the series starts) would
        # otherwise silently snap to the series' first point.
        if abs((chronological[idx0].as_of - event.as_of).days) > _EVENT_MATCH_TOLERANCE_DAYS:
            continue
        if chronological[idx0].close == 0 or idx0 + longest >= len(chronological):
            continue
        returns = {
            s: (chronological[idx0 + s].close / chronological[idx0].close) - 1.0
            for s in _EVENT_BETA_SESSIONS
        }
        per_event.append(
            {
                "event_date": event.as_of.isoformat(),
                "surprise_pct": event.surprise_pct,
                "is_beat": event.surprise_pct > 0,
                "forward_return_by_session": {str(s): r for s, r in returns.items()},
            }
        )

    result: dict[str, Any] = {
        "sessions": list(_EVENT_BETA_SESSIONS),
        "n_observations": len(per_event),
        "observations": per_event,
        "small_sample_note": (
            f"only {len(per_event)} qualifying print(s) fall inside the fetched price "
            f"history (framework anchor is a sample of {_EVENT_BETA_MIN_SAMPLE}) — too few "
            "to treat as a reliable distribution; read individually, not as a statistic"
        )
        if len(per_event) < _EVENT_BETA_MIN_SAMPLE
        else None,
        "raw_return_note": (
            "these are RAW forward returns, not beta-adjusted against the market — a "
            "print during a broad market selloff/rally will look worse/better than the "
            "stock's own idiosyncratic reaction to the print"
        ),
        "announcement_timing_note": (
            "the earnings-date provider carries no before/after-market flag — a "
            "before-open print's 1-session return is the reaction itself, while an "
            "after-close print's 1-session return is one day of drift AFTER the "
            "reaction (which happened in the prior close-to-close gap); these are "
            "mixed together indiscriminately below"
        ),
    }
    for s in _EVENT_BETA_SESSIONS:
        all_returns = [o["forward_return_by_session"][str(s)] for o in per_event]
        beat_returns = [
            o["forward_return_by_session"][str(s)] for o in per_event if o["is_beat"]
        ]
        stats: dict[str, Any] = {
            "mean_signed_return": sum(all_returns) / len(all_returns) if all_returns else None,
            "min_return": min(all_returns) if all_returns else None,
            "max_return": max(all_returns) if all_returns else None,
            "stdev_return": (statistics.stdev(all_returns) if len(all_returns) >= 2 else None),
            "mean_absolute_return_after_beats": (
                sum(abs(r) for r in beat_returns) / len(beat_returns) if beat_returns else None
            ),
            "n_beats": len(beat_returns),
        }
        result[f"{s}_session"] = stats
    return result


def _upcoming_earnings(events: list[EarningsEvent], as_of: date) -> dict[str, Any]:
    """The nearest scheduled-but-not-yet-reported print, if the provider has
    one — not a full catalyst calendar (index reviews, lock-up expiries,
    guidance events are not in scope for this project, see
    attribution/events.py's module docstring), just what yfinance's earnings
    calendar already discloses about the next date."""
    future = sorted(
        (e for e in events if e.eps_reported is None and e.as_of >= as_of),
        key=lambda e: e.as_of,
    )
    if not future:
        return _unavailable("no scheduled future earnings date in the provider's data")
    nxt = future[0]
    return {
        "available": True,
        "date": nxt.as_of.isoformat(),
        "eps_estimate": nxt.eps_estimate,
        "note": "a scheduled date from the data provider, not a confirmed company disclosure",
    }


def _build_factor_panel(
    ticker: str, peer_group: dict[str, FactorContext], factor_config: dict[str, FactorDefinition]
) -> list[dict[str, Any]]:
    panel: list[dict[str, Any]] = []
    for name in sorted(factor_config):
        definition = factor_config[name]
        raw = compute_factor(name, peer_group[ticker])
        entry: dict[str, Any] = {
            "name": name,
            "channel": definition.channel,
            "horizon": definition.horizon,
            "expected_direction": definition.expected_direction,
            "weight": definition.weight,
            "description": definition.description,
        }
        if raw is None:
            entry["available"] = False
            entry["reason"] = "not computable from available data for this ticker"
            panel.append(entry)
            continue

        peer_raw_values = {}
        for peer_ticker, peer_ctx in peer_group.items():
            peer_value = compute_factor(name, peer_ctx)
            if peer_value is not None:
                peer_raw_values[peer_ticker] = peer_value.value
        z = winsorized_zscore(ticker, peer_raw_values)

        entry["available"] = True
        entry["raw"] = _jsonable(raw)
        if z is not None:
            entry["z_score"] = z.z_score
            entry["peer_count"] = z.peer_count
            entry["winsorized"] = z.winsorized
            entry["directional_z"] = z.z_score * definition.expected_direction
        else:
            entry["z_score"] = None
            entry["reason_no_zscore"] = "insufficient peers for this (sector, region) bucket"
        panel.append(entry)
    return panel


def _build_valuation(ticker: str, yfin: YFinanceProvider) -> dict[str, Any]:
    """cost_of_equity and multiples_vs_peers are computed independently of
    the DCF/reverse-DCF/scenario block below — found in review that
    gather_valuation_inputs' currency-consistency refusal (a real, correct
    guard for the FCF-based models) was ALSO silently discarding these two
    independently-computable sections, even for a ticker (1299.HK) where
    compare_multiples() succeeds entirely on its own. Each sub-section now
    degrades on its own."""
    valuation_config = load_valuation_config()
    section: dict[str, Any] = {"available": True}

    coe = None
    try:
        snapshot = yfin.get_snapshot(ticker)
        risk_free_rate = yfin.get_risk_free_rate()
        ad_hoc_ctx = FactorContext(
            ticker=ticker, as_of=snapshot.as_of, snapshot=snapshot, price_history=None
        )
        beta = snapshot_field(ad_hoc_ctx, "beta")
        if beta is None:
            section["cost_of_equity"] = _unavailable("no beta reported for this ticker")
        else:
            coe = cost_of_equity(
                risk_free_rate=risk_free_rate.value,
                beta=beta,
                equity_risk_premium=valuation_config.equity_risk_premium,
                min_beta=valuation_config.min_beta,
                max_beta=valuation_config.max_beta,
            )
            snapshot_currency = snapshot.value.get("currency")
            section["cost_of_equity"] = {
                "rate": coe.rate,
                "risk_free_rate": _jsonable(risk_free_rate),
                "effective_beta": coe.effective_beta,
                "raw_beta": coe.raw_beta,
                "was_clamped": coe.was_clamped,
                "equity_risk_premium": valuation_config.equity_risk_premium,
                "risk_free_rate_currency_note": (
                    "risk-free rate is the US 10Y Treasury (^TNX) — no per-market rate is "
                    "wired up yet, so this is a rough approximation for a "
                    f"{snapshot_currency}-denominated company, not a {snapshot_currency} "
                    "market rate"
                )
                if snapshot_currency is not None and snapshot_currency != "USD"
                else None,
                "staleness_days_vs_fundamentals": abs(
                    (risk_free_rate.as_of - snapshot.as_of).days
                ),
                "beta_reconciliation_note": (
                    "this is the single beta yfinance reports for the stock, used only "
                    "for the discount rate — it is NOT the same figure as "
                    "attribution.market.beta, which is fitted empirically over the "
                    "attribution window. The two can legitimately differ; neither "
                    "corrects the other."
                ),
            }
    except ProviderError as exc:
        section["cost_of_equity"] = _unavailable(str(exc))

    try:
        multiples = compare_multiples(ticker, yfin)
        section["multiples_vs_peers"] = [_jsonable(m) for m in multiples]
    except ProviderError as exc:
        section["multiples_vs_peers"] = _unavailable(str(exc))
    section["multiples_vs_own_history_note"] = (
        "not implemented — needs a point-in-time fundamentals store accumulated over "
        "years, which this project has not built yet (see valuation/multiples.py)"
    )

    try:
        inputs = gather_valuation_inputs(ticker, yfin)
    except (ValuationInputError, ProviderError) as exc:
        reason = str(exc)
        section["dcf"] = _unavailable(reason)
        section["reverse_dcf"] = _unavailable(reason)
        section["scenarios"] = _unavailable(reason)
        return section

    if coe is None:
        reason = "discount rate unavailable — see valuation.cost_of_equity for why"
        section["dcf"] = _unavailable(reason)
        section["reverse_dcf"] = _unavailable(reason)
        section["scenarios"] = _unavailable(reason)
        return section
    discount_rate = coe.rate

    section["currency"] = inputs.currency
    section["current_price"] = inputs.current_price
    section["fcfe0"] = inputs.fcfe0
    section["fcfe0_negative_note"] = (
        "trailing free cash flow is negative — growing it under positive growth "
        "makes it MORE negative, so any DCF value below reflects that finding, "
        "not a literal forecast of a growing, cash-generative business"
        if inputs.fcfe0 <= 0
        else None
    )

    try:
        growth = base_case_growth(inputs)
        assumptions = DcfAssumptions(
            fcfe0=inputs.fcfe0,
            shares_outstanding=inputs.shares_outstanding,
            growth_start=growth,
            terminal_growth=valuation_config.terminal_growth,
            cap_years=valuation_config.cap_years_default,
            discount_rate=discount_rate,
        )
        dcf_value = dcf_value_per_share(assumptions)
        section["dcf"] = {
            "available": True,
            "value_per_share": dcf_value,
            "growth_start": growth,
            "terminal_growth": valuation_config.terminal_growth,
            "cap_years": valuation_config.cap_years_default,
            "discount_rate": discount_rate,
            "gap_vs_price": (dcf_value / inputs.current_price) - 1.0,
            "growth_basis_note": (
                "growth_start is the company's own trailing revenue growth taken as-is, "
                "not an independent forecast — see reverse_dcf for what the price implies"
            ),
        }
    except (ValuationInputError, DcfInputError) as exc:
        section["dcf"] = _unavailable(str(exc))

    try:
        reverse = solve_implied_growth(
            current_price=inputs.current_price,
            fcfe0=inputs.fcfe0,
            shares_outstanding=inputs.shares_outstanding,
            terminal_growth=valuation_config.terminal_growth,
            cap_years=valuation_config.cap_years_default,
            discount_rate=discount_rate,
            min_growth=valuation_config.reverse_dcf_min_growth,
            max_growth=valuation_config.reverse_dcf_max_growth,
        )
        cells = sensitivity_table(
            current_price=inputs.current_price,
            fcfe0=inputs.fcfe0,
            shares_outstanding=inputs.shares_outstanding,
            base_terminal_growth=valuation_config.terminal_growth,
            cap_years=valuation_config.cap_years_default,
            base_discount_rate=discount_rate,
            min_growth=valuation_config.reverse_dcf_min_growth,
            max_growth=valuation_config.reverse_dcf_max_growth,
            discount_rate_deltas=valuation_config.sensitivity_discount_rate_deltas,
            terminal_growth_deltas=valuation_config.sensitivity_terminal_growth_deltas,
        )
        section["reverse_dcf"] = {
            "available": True,
            "implied_growth": reverse.implied_growth,
            "terminal_growth": reverse.terminal_growth,
            "cap_years": reverse.cap_years,
            "discount_rate": reverse.discount_rate,
            "margin_note": reverse.margin_note,
            "sensitivity": [_jsonable(c) for c in cells],
        }
    except (NoImpliedGrowthInRange, DcfInputError) as exc:
        section["reverse_dcf"] = _unavailable(str(exc))

    try:
        analysis = run_scenarios(
            base_growth=base_case_growth(inputs),
            base_terminal_growth=valuation_config.terminal_growth,
            base_cap_years=valuation_config.cap_years_default,
            discount_rate=discount_rate,
            fcfe0=inputs.fcfe0,
            shares_outstanding=inputs.shares_outstanding,
            scenarios=valuation_config.scenarios,
            cap_years_bounds=(valuation_config.cap_years_min, valuation_config.cap_years_max),
        )
        section["scenarios"] = {
            "available": True,
            "results": [_jsonable(r) for r in analysis.results],
            "probability_weighted_value": analysis.probability_weighted_value,
            "gap_vs_price": (
                (analysis.probability_weighted_value / inputs.current_price) - 1.0
                if analysis.probability_weighted_value is not None
                else None
            ),
        }
    except (ScenarioProbabilityError, ValuationInputError) as exc:
        section["scenarios"] = _unavailable(str(exc))

    try:
        multiples = compare_multiples(ticker, yfin)
        section["multiples_vs_peers"] = [_jsonable(m) for m in multiples]
    except ProviderError as exc:
        section["multiples_vs_peers"] = _unavailable(str(exc))
    section["multiples_vs_own_history_note"] = (
        "not implemented — needs a point-in-time fundamentals store accumulated over "
        "years, which this project has not built yet (see valuation/multiples.py)"
    )

    return section


def _build_event_data(
    ticker: str, yfin: YFinanceProvider, as_of: date, window_days: int
) -> dict[str, Any]:
    """Earnings event tape, event-beta stats, and the next scheduled print —
    built independently of the market/sector regression below. Found in
    review that building this only as a byproduct of a successful regression
    meant a market-index-configuration gap or a thin-data InsufficientDataError
    silently deleted the entire earnings catalyst calendar along with the
    regression output, even though the earnings data was independently
    fetchable — the two do not depend on each other and must not fail
    together."""
    try:
        earnings = yfin.get_earnings_history(ticker)
        full_history = yfin.get_price_history(ticker)
    except ProviderError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "matches": [],
            "event_tape": [],
            "event_tape_source_url_note": None,
            "event_beta_stats": _unavailable(str(exc)),
            "upcoming_earnings": _unavailable(str(exc)),
        }
    matches = earnings_events_in_window(earnings.value, as_of, window_days)
    return {
        "available": True,
        "reason": None,
        "matches": matches,
        "event_tape": [
            {
                "as_of": m.event.as_of.isoformat(),
                "eps_estimate": m.event.eps_estimate,
                "eps_reported": m.event.eps_reported,
                "surprise_pct": m.event.surprise_pct,
                "days_before_as_of": m.days_before_as_of,
                "source_url": earnings.url,
            }
            for m in matches
        ],
        "event_tape_source_url_note": (
            "source_url is the shared provenance URL for the whole earnings history "
            "(the ticker's quote page), not a per-event news article — no per-event "
            "source document is available from this provider"
        ),
        "event_beta_stats": _event_beta_stats(earnings.value, full_history.value, as_of),
        "upcoming_earnings": _upcoming_earnings(earnings.value, as_of),
    }


def _build_attribution(
    ticker: str, region: str, yfin: YFinanceProvider, window_days: int, as_of: date
) -> dict[str, Any]:
    event_data = _build_event_data(ticker, yfin, as_of, window_days)
    event_fields = {
        "event_tape": event_data["event_tape"],
        "event_tape_source_url_note": event_data["event_tape_source_url_note"],
        "event_beta_stats": event_data["event_beta_stats"],
        "upcoming_earnings": event_data["upcoming_earnings"],
    }

    attribution_config = load_attribution_config()
    market_index = attribution_config.market_index_by_region.get(region)
    if market_index is None:
        return {
            **_unavailable(f"no market index configured for region {region!r}"),
            **event_fields,
        }

    lookback = attribution_config.lookback_months
    try:
        stock_history = yfin.get_price_history(ticker, months=lookback)
        market_history = yfin.get_price_history(market_index, months=lookback)
        snapshot = yfin.get_snapshot(ticker)
    except ProviderError as exc:
        return {**_unavailable(str(exc)), **event_fields}

    sector = snapshot.value.get("sector")
    sector_points = None
    sector_unavailable_reason: str | None = None
    if region != "US":
        sector_unavailable_reason = f"no sector index proxy configured for region {region!r}"
    elif sector is None:
        sector_unavailable_reason = "no sector reported for this ticker"
    elif sector not in attribution_config.sector_etf_by_us_sector:
        sector_unavailable_reason = f"no ETF mapping configured for sector {sector!r}"
    else:
        sector_etf = attribution_config.sector_etf_by_us_sector[sector]
        try:
            sector_points = yfin.get_price_history(sector_etf, months=lookback).value
        except ProviderError as exc:
            sector_unavailable_reason = f"sector ETF {sector_etf} unavailable: {exc}"

    try:
        result = decompose(
            ticker=ticker,
            as_of=stock_history.as_of,
            window_days=window_days,
            stock_points=stock_history.value,
            market_points=market_history.value,
            min_observations=attribution_config.min_observations,
            sector_points=sector_points,
            sector_unavailable_reason=sector_unavailable_reason,
        )
    except InsufficientDataError as exc:
        return {**_unavailable(str(exc)), **event_fields}

    # A fetch FAILURE for the earnings history must never be silently
    # treated as "we looked and there genuinely was no event" — found in
    # review that both cases fed `label_confidence` an empty `matches` list
    # and produced an identical, indistinguishable "Unexplained" verdict.
    if event_data["available"]:
        matches = event_data["matches"]
        confidence, confidence_reason = label_confidence(result.residual, matches)
        channel, channel_reason = classify_channel(result, confidence)
    else:
        matches = []
        confidence = None
        confidence_reason = f"earnings history unavailable: {event_data['reason']}"
        channel = None
        channel_reason = "cannot classify without the earnings event tape"

    return {
        "available": True,
        "window_days": window_days,
        "as_of": result.as_of.isoformat(),
        "stock_return": result.stock_return,
        "n_observations": result.n_observations,
        "r_squared": result.r_squared,
        "r_squared_low_note": (
            "the market/sector regression has low explanatory power — treat the "
            "market/sector split as a rough signal, not a precise one"
        )
        if result.r_squared < 0.3
        else None,
        "index_endogeneity_note": (
            "the index used is not adjusted to exclude this stock — for a large index "
            "constituent this inflates the fitted beta and understates the residual"
        ),
        "alpha_component": result.alpha_component,
        "market": {
            "beta": result.market_beta,
            "index_return": result.market_return,
            "component": result.market_component,
            "beta_reconciliation_note": (
                "this beta is fitted empirically over the attribution regression's own "
                f"lookback window ({lookback} months against {market_index}) — it is NOT "
                "the same figure as valuation.cost_of_equity.effective_beta, which is the "
                "single beta yfinance reports for the stock. The two can legitimately "
                "differ; neither corrects the other."
            ),
        },
        "sector": {
            "available": result.sector_available,
            "beta": result.sector_beta,
            "index_return": result.sector_return,
            "component": result.sector_component,
            "reason": result.sector_unavailable_reason,
        },
        "style": {
            "available": False,
            "reason": "needs a broader universe than this pilot has",
        },
        "residual": result.residual,
        "unexplained_share": result.unexplained_share,
        "confidence": confidence.value if confidence is not None else None,
        "confidence_reason": confidence_reason,
        "channel": channel.value if channel is not None else None,
        "channel_reason": channel_reason,
        "channel_method_note": (
            "channel classification uses a coarse heuristic (confirmed-earnings-surprise "
            "sign, or how much of the move market/sector beta already explains) — not the "
            "framework's ideal method of comparing point-in-time consensus EPS before vs. "
            "after the move, which needs an accumulated estimates history this project "
            "does not have"
        ),
        **event_fields,
        "large_unexplained_move_flag": (
            confidence is not None
            and confidence.value == "Unexplained"
            and abs(result.stock_return) >= attribution_config.large_unexplained_move_threshold
        ),
    }


def _build_decision(
    ticker: str,
    region: str,
    peer_group: dict[str, FactorContext],
    yfin: YFinanceProvider,
    record: TickerRecord,
    existing_position: bool,
    decision_config: DecisionRulesConfig,
) -> dict[str, Any]:
    ctx = peer_group[ticker]
    info = ctx.snapshot.value

    price_conflict = next((c for c in record.quality.conflicts if c.field == "price"), None)
    if "price" in record.quality.missing_fields:
        sources_agreeing = 0
    elif "price" in record.quality.single_sourced_fields:
        sources_agreeing = 1
    else:
        sources_agreeing = 2

    universe_raw = yaml.safe_load(Path("config/universe.yaml").read_text())
    market_accessible = universe_raw.get("markets", {}).get(region, {}).get("accessible")

    ocf = info.get("operatingCashflow")
    net_income = info.get("netIncomeToCommon")
    ocf_to_ni = (
        float(ocf) / float(net_income) if ocf is not None and net_income not in (None, 0) else None
    )
    total_debt = info.get("totalDebt")
    total_cash = info.get("totalCash")
    net_debt = (
        float(total_debt) - float(total_cash)
        if total_debt is not None and total_cash is not None
        else None
    )
    ebitda = info.get("ebitda")
    price = info.get("regularMarketPrice")
    if price is None:
        price = info.get("previousClose")

    fundamentals_as_of = None
    for ts in (info.get("mostRecentQuarter"), info.get("lastFiscalYearEnd")):
        if ts is not None:
            fundamentals_as_of = datetime.fromtimestamp(ts, tz=UTC).date()
            break

    average_daily_dollar_volume = None
    average_volume = info.get("averageVolume")
    quote_currency = info.get("currency")
    if average_volume is not None and price is not None and quote_currency is not None:
        native_adv = float(average_volume) * float(price)
        if quote_currency == "USD":
            average_daily_dollar_volume = native_adv
        else:
            try:
                fx = yfin.get_fx_rate(quote_currency, "USD")
                average_daily_dollar_volume = native_adv * fx.value
            except ProviderError:
                average_daily_dollar_volume = None

    gate_ctx = GateContext(
        ticker=ticker,
        now=ctx.as_of,
        sources_agreeing=sources_agreeing,
        price_disagreement_pct=price_conflict.pct_spread if price_conflict else None,
        fundamentals_as_of=fundamentals_as_of,
        currency_resolved=record.currency is not None,
        ocf_to_ni_trailing=ocf_to_ni,
        net_debt=net_debt,
        ebitda=float(ebitda) if ebitda is not None else None,
        sector=info.get("sector"),
        average_daily_dollar_volume=average_daily_dollar_volume,
        market_accessible=market_accessible,
        reference_position_usd=(
            decision_config.sizing.reference_portfolio_value_usd
            * (decision_config.sizing.base_weight_pct / 100.0)
        ),
    )
    gate_trace = run_all_gates(gate_ctx, decision_config)

    l_composite = compute_composite(
        "L", decision_config.scoring.long_horizon_weights, LONG_HORIZON_FACTOR_MAP, ctx, peer_group
    )
    s_composite = compute_composite(
        "S",
        decision_config.scoring.short_horizon_weights,
        SHORT_HORIZON_FACTOR_MAP,
        ctx,
        peer_group,
    )
    conviction: float | None = None
    if l_composite.value is not None:
        conviction = max(
            decision_config.scoring.conviction_min,
            min(decision_config.scoring.conviction_max, gate_trace.data_completeness),
        )

    position = PositionContext.EXISTING if existing_position else PositionContext.NEW
    action, action_reason = decide_action(
        l_composite.value, conviction, gate_trace, decision_config, position
    )
    overlay = timing_overlay(l_composite.value, s_composite.value)

    sizing_section: dict[str, Any]
    if action.value in _SIZEABLE_ACTIONS:
        attribution_config = load_attribution_config()
        market_index = attribution_config.market_index_by_region.get(region)
        try:
            stock_hist = yfin.get_price_history(ticker).value
            market_hist = yfin.get_price_history(market_index).value if market_index else []
            sizing = compute_sizing(
                conviction=conviction,
                regime_multiplier=1.0,
                stock_points=stock_hist,
                market_points=market_hist,
                as_of=ctx.as_of,
                average_daily_dollar_volume=gate_ctx.average_daily_dollar_volume,
                config=decision_config.sizing,
                min_adv_multiple=decision_config.gates.investability.min_adv_multiple_of_position,
            )
            sizing_section = _jsonable(sizing)
            sizing_section["regime_multiplier_note"] = (
                "R (regime multiplier) has no detection module yet — held at the "
                "neutral value 1.0, not a computed regime read"
            )
        except ProviderError as exc:
            sizing_section = _unavailable(str(exc))
    else:
        sizing_section = _unavailable(f"action {action.value!r} is not a sizeable action")

    return {
        "gates": {
            "veto": gate_trace.veto,
            "veto_reasons": gate_trace.veto_reasons,
            "data_completeness": gate_trace.data_completeness,
            "results": [_jsonable(r) for r in gate_trace.results],
        },
        "scores": {
            "L": _jsonable(l_composite),
            "S": _jsonable(s_composite),
            "R": {
                "value": 1.0,
                "note": (
                    "no regime-detection module exists yet — held at the neutral value "
                    "1.0, not a computed regime read"
                ),
            },
            "conviction": conviction,
        },
        "action": {
            "position_context": position.value,
            "value": action.value,
            "reason": action_reason,
        },
        "timing_overlay": overlay,
        "sizing": sizing_section,
    }


def build_report_payload(
    ticker: str,
    existing_position: bool = False,
    window_days: int = 30,
    offline_fixtures: bool = False,
    base_currency: str = "USD",
) -> dict[str, Any]:
    """The complete, self-contained payload for one ticker. Every number in
    it traces to a Python computation performed by an M1-M5 module; nothing
    here is estimated or invented for the reasoning layer's convenience."""
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    stooq = StooqProvider(offline_fixtures_dir=fixtures_dir)
    cache = None if offline_fixtures else Cache(DEFAULT_CACHE_PATH)

    try:
        record = fetch_ticker(
            ticker, yfinance=yfin, stooq=stooq, base_currency=base_currency, cache=cache
        )
        peer_group = build_peer_group(ticker, yfin)

        ctx = peer_group[ticker]
        region = resolve_market(ticker)
        factor_config = load_factor_config()
        validate_registry_matches_config(factor_config)
        decision_config = load_decision_rules()

        price_section: dict[str, Any] = {
            "price_native": _jsonable(record.price_native),
            "price_base": _jsonable(record.price_base),
            "currency": _jsonable(record.currency),
            "shares_outstanding": _jsonable(record.shares_outstanding),
            "company_name": _jsonable(record.company_name),
        }
        currency_code = record.currency.value if record.currency else None
        try:
            history = yfin.get_price_history(ticker)
            summary = _price_history_summary(
                history.value, ctx.as_of, history.source, currency_code
            )
            lookback_days = decision_config.sizing.vol_ratio_lookback_days
            summary["realised_vol_annualised"] = realized_vol(
                history.value, ctx.as_of, lookback_days
            )
            summary["realised_vol_lookback_days"] = lookback_days
            price_section["history"] = summary
        except ProviderError as exc:
            price_section["history"] = _unavailable(str(exc))

        # `trailingAnnualDividendYield` (a decimal fraction) not `dividendYield`
        # (a PERCENT) — found in review that the raw percent field was emitted
        # unconverted next to every other rate in this payload expressed as a
        # fraction (cost_of_equity.rate, reverse_dcf.implied_growth,
        # history.return_1m...), a 100x unit trap for the reasoning layer.
        # Routed through snapshot_field rather than a raw dict read so a NaN
        # snapshot value (yfinance's known failure mode for a field that
        # hasn't finalized) is stripped to None instead of crossing into the
        # JSON payload as an invalid `NaN` token.
        price_section["dividend_yield"] = snapshot_field(ctx, "trailingAnnualDividendYield")
        price_section["dividend_history_note"] = (
            "only the current trailing dividend yield is available (a single "
            "point-in-time snapshot field, decimal fraction) — a full dividend or "
            "share-count-change history needs a point-in-time fundamentals store "
            "this project has not built yet, the same gap disclosed for "
            "own-history multiples"
        )

        recommendation_key = ctx.snapshot.value.get("recommendationKey")
        number_of_analyst_opinions = snapshot_field(ctx, "numberOfAnalystOpinions")
        consensus_section: dict[str, Any] = {
            "available": True,
            "target_high": snapshot_field(ctx, "targetHighPrice"),
            "target_low": snapshot_field(ctx, "targetLowPrice"),
            "target_mean": snapshot_field(ctx, "targetMeanPrice"),
            "number_of_analyst_opinions": (
                int(number_of_analyst_opinions)
                if number_of_analyst_opinions is not None
                else None
            ),
            "recommendation_key": (
                recommendation_key if isinstance(recommendation_key, str) else None
            ),
            "recommendation_mean": snapshot_field(ctx, "recommendationMean"),
            "note": (
                "a point-in-time snapshot of current analyst posture, not a revision "
                "history — this project has no accumulated point-in-time estimates "
                "store, so 'has consensus moved' cannot be answered, only 'where does "
                "consensus stand right now'"
            ),
        }
        # Found in review: the previous all-None check only looked at 4 of
        # the 6 fields, so a ticker with ONLY recommendation_mean/
        # number_of_analyst_opinions populated was marked wholly unavailable
        # and those two real values were discarded.
        consensus_value_keys = (
            "target_high",
            "target_low",
            "target_mean",
            "number_of_analyst_opinions",
            "recommendation_key",
            "recommendation_mean",
        )
        if all(consensus_section[k] is None for k in consensus_value_keys):
            consensus_section = _unavailable("no analyst estimate fields reported for this ticker")

        try:
            decision_section = _build_decision(
                ticker, region, peer_group, yfin, record, existing_position, decision_config
            )
        except ProviderError as exc:
            decision_section = _unavailable(str(exc))

        # Top-level per-section guard (CLAUDE.md rule 4 / this module's own
        # "each section degrades independently" invariant): an unexpected
        # exception in any ONE section must not delete the whole payload,
        # including the sections that already succeeded. Each of these three
        # builders already handles its own expected ProviderError/
        # ValuationInputError internally — this is the backstop for anything
        # they don't, so a bug in one analytical section can never silently
        # cost the reasoning layer every other section too.
        try:
            factors_section: list[dict[str, Any]] | dict[str, Any] = _build_factor_panel(
                ticker, peer_group, factor_config
            )
        except Exception as exc:  # noqa: BLE001
            factors_section = [_unavailable(f"{type(exc).__name__}: {exc}")]
        try:
            valuation_section = _build_valuation(ticker, yfin)
        except Exception as exc:  # noqa: BLE001
            valuation_section = _unavailable(f"{type(exc).__name__}: {exc}")
        try:
            attribution_section = _build_attribution(
                ticker, region, yfin, window_days, ctx.as_of
            )
        except Exception as exc:  # noqa: BLE001
            attribution_section = _unavailable(f"{type(exc).__name__}: {exc}")
    finally:
        if cache is not None:
            cache.close()

    payload: dict[str, Any] = {
        "meta": {
            "ticker": ticker,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "as_of": ctx.as_of.isoformat(),
            "base_currency": base_currency,
            "existing_position": existing_position,
            "attribution_window_days": window_days,
            "currency_scope_note": (
                f"only price/price_native/price_base are normalised to {base_currency} — "
                "valuation stays in the company's own reporting currency and the decision "
                "engine's liquidity/sizing figures are USD, matching each underlying "
                "module's existing behaviour"
            ),
        },
        "identity": {
            "sector": ctx.snapshot.value.get("sector"),
            "region": region,
            "peers": sorted(t for t in peer_group if t != ticker),
        },
        "price": price_section,
        "consensus": consensus_section,
        "data_quality": {
            "completeness": record.quality.completeness,
            "fields_present": record.quality.fields_present,
            "fields_expected": record.quality.fields_expected,
            "conflicts": [_jsonable(c) for c in record.quality.conflicts],
            "single_sourced_fields": record.quality.single_sourced_fields,
            "missing_fields": record.quality.missing_fields,
            "provider_errors": record.errors,
            "completeness_reconciliation_note": (
                "this completeness score covers only the 5 core M1 fetch fields (price, "
                "currency, shares_outstanding, eps_ttm, company_name) — it is a DIFFERENT "
                "measurement from decision.gates.data_completeness, which scores how many "
                "of the Stage-1 gate sub-checks could be evaluated at all. A ticker can "
                "score 1.0 here and well below 1.0 there; neither figure supersedes the "
                "other."
            ),
        },
        "factors": factors_section,
        "valuation": valuation_section,
        "attribution": attribution_section,
        "decision": decision_section,
        "disclaimer": (
            "This is research and decision support, not investment advice. "
            "All views are probabilistic and may be wrong."
        ),
    }
    return payload
