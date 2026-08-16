"""Stock Analyzer CLI. M1: `analyze fetch`. M2: `analyze factors`."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from . import factors as _factors  # noqa: F401  (side effect: registers every factor)
from .attribution.channel import classify_channel
from .attribution.config import load_attribution_config
from .attribution.decompose import InsufficientDataError, decompose
from .attribution.events import earnings_events_in_window, label_confidence
from .backtest.config import load_backtest_config
from .backtest.costs import round_trip_costs
from .backtest.engine import MomentumConfig, PointInTimeSeries, RebalanceStep, run_walk_forward
from .backtest.metrics import deflated_sharpe_ratio, sharpe_ratio, split_by_volatility_regime
from .data.base import ProviderError
from .data.cache import Cache
from .data.fetch import fetch_ticker
from .data.normalize import resolve_market
from .data.provider_config import load_providers_config
from .data.providers.stooq_provider import StooqProvider
from .data.providers.yfinance_provider import YFinanceProvider
from .decision.config import load_decision_rules
from .decision.gates import GateContext, run_all_gates
from .decision.scoring import (
    LONG_HORIZON_FACTOR_MAP,
    SHORT_HORIZON_FACTOR_MAP,
    compute_composite,
)
from .decision.sizing import compute_sizing
from .decision.tree import PositionContext, decide_action, timing_overlay
from .factors.peers import build_peer_group
from .factors.registry import (
    FactorContext,
    compute_factor,
    load_factor_config,
    validate_registry_matches_config,
    winsorized_zscore,
)
from .factors.util import snapshot_field
from .predictions.config import load_predictions_config
from .predictions.contract import PredictionContractError, prediction_from_json
from .predictions.log import append_predictions, read_predictions
from .predictions.score import ScoreBucket, score_predictions, yfinance_price_lookup
from .report.builder import build_report_payload
from .valuation.config import load_valuation_config
from .valuation.dcf import DcfAssumptions, DcfInputError, cost_of_equity, dcf_value_per_share
from .valuation.inputs import (
    ValuationInputError,
    base_case_growth,
    gather_valuation_inputs,
)
from .valuation.reverse_dcf import NoImpliedGrowthInRange, sensitivity_table, solve_implied_growth
from .valuation.scenarios import ScenarioProbabilityError, run_scenarios

app = typer.Typer(help="Stock Analyzer — dual-horizon equity analysis on free-tier data.")
console = Console()

DEFAULT_FIXTURES_DIR = Path("tests/fixtures")
DEFAULT_CACHE_PATH = Path("data/cache.db")


@app.callback()
def main() -> None:
    """Forces subcommand dispatch (e.g. `analyze fetch ...`) even while `fetch` is
    the only command — more land in later milestones (`factors`, `why`, `decide`)."""


@app.command()
def fetch(
    tickers: list[str] = typer.Argument(  # noqa: B008
        ..., help="Tickers, e.g. AAPL 7203.T ASML.AS 1299.HK"
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
    base_currency: str = typer.Option(
        "USD", "--base-currency", help="Currency to normalise prices into."
    ),
) -> None:
    """Fetch clean, currency-normalised, provenance-tagged data for each ticker."""
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    stooq = StooqProvider(offline_fixtures_dir=fixtures_dir)
    # Offline-fixture mode is for deterministic testing — bypass the cache so a
    # test run never reads or pollutes the real cache.db. CLAUDE.md §3.1 rule 5.
    cache = None if offline_fixtures else Cache(DEFAULT_CACHE_PATH)

    table = Table(title=f"analyze fetch — base currency {base_currency}")
    table.add_column("Ticker")
    table.add_column("Price (native)")
    table.add_column("Price (base)")
    table.add_column("Currency")
    table.add_column("Shares Out")
    table.add_column("Completeness")
    table.add_column("Conflicts")

    had_errors = False
    for ticker in tickers:
        record = fetch_ticker(
            ticker, yfinance=yfin, stooq=stooq, base_currency=base_currency, cache=cache
        )
        if record.quality.completeness == 0.0:
            had_errors = True
        table.add_row(
            record.ticker,
            f"{record.price_native.value:,.2f} {record.currency.value}"
            if record.price_native and record.currency
            else "—",
            f"{record.price_base.value:,.2f} {base_currency}" if record.price_base else "—",
            record.currency.value if record.currency else "—",
            f"{record.shares_outstanding.value:,.0f}" if record.shares_outstanding else "—",
            f"{record.quality.completeness:.0%}",
            str(len(record.quality.conflicts)),
        )
        for err in record.errors:
            console.print(f"  [yellow]note[/yellow] {record.ticker}: {err}")

    console.print(table)
    if cache is not None:
        cache.close()
    if had_errors:
        raise typer.Exit(code=1)


@app.command()
def factors(
    ticker: str = typer.Argument(..., help="Ticker, e.g. AAPL"),  # noqa: B008
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Print every registered factor for TICKER: raw value, z-score, channel,
    horizon, expected direction, and source."""
    config = load_factor_config()
    validate_registry_matches_config(config)

    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)

    try:
        peer_group = build_peer_group(ticker, yfin)
    except Exception as exc:
        console.print(f"[red]FAIL[/red] {ticker}: could not build snapshot — {exc}")
        raise typer.Exit(code=1) from None

    ctx = peer_group[ticker]
    peer_count = len(peer_group)

    table = Table(title=f"analyze factors {ticker} — peer bucket size {peer_count}")
    table.add_column("Factor")
    table.add_column("Value")
    table.add_column("Z-score")
    table.add_column("Signal")
    table.add_column("Channel")
    table.add_column("Horizon")
    table.add_column("Direction")
    table.add_column("Source")

    for name in sorted(config):
        definition = config[name]
        raw = compute_factor(name, ctx)
        if raw is None:
            row_direction = "+" if definition.expected_direction == 1 else "-"
            table.add_row(
                name, "—", "—", "—", definition.channel, definition.horizon, row_direction, "—"
            )
            continue

        peer_raw_values: dict[str, float] = {}
        for peer_ticker, peer_ctx in peer_group.items():
            peer_value = compute_factor(name, peer_ctx)
            if peer_value is not None:
                peer_raw_values[peer_ticker] = peer_value.value

        z = winsorized_zscore(ticker, peer_raw_values)
        if z is not None:
            z_display = f"{z.z_score:+.2f} (n={z.peer_count})"
            # z * expected_direction: a factor's raw z can be high while the
            # *expected* price action is down (contrarian factors) — found in
            # review that printing raw z next to a separate direction column
            # is the likeliest place for that sign to get lost downstream.
            # This column folds them together into one directly-readable number.
            signal_display = f"{z.z_score * definition.expected_direction:+.2f}"
        else:
            z_display = "insufficient peers"
            signal_display = "—"

        table.add_row(
            name,
            f"{raw.value:+.4f}",
            z_display,
            signal_display,
            definition.channel,
            definition.horizon,
            "+" if definition.expected_direction == 1 else "-",
            raw.source,
        )

    console.print(table)


@app.command()
def value(
    ticker: str = typer.Argument(..., help="Ticker, e.g. AAPL"),  # noqa: B008
    reverse: bool = typer.Option(
        False, "--reverse", help="Solve for the growth rate the current price implies."
    ),
    scenarios: bool = typer.Option(
        False, "--scenarios", help="Bull/base/bear scenarios with a probability-weighted value."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Forward DCF by default. --reverse states the implied growth rate with a
    sensitivity table. --scenarios runs bull/base/bear with a weighted value."""
    valuation_config = load_valuation_config()
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)

    try:
        inputs = gather_valuation_inputs(ticker, yfin)
        risk_free_rate = yfin.get_risk_free_rate()
    except (ValuationInputError, ProviderError) as exc:
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None

    coe = cost_of_equity(
        risk_free_rate=risk_free_rate.value,
        beta=inputs.beta,
        equity_risk_premium=valuation_config.equity_risk_premium,
        min_beta=valuation_config.min_beta,
        max_beta=valuation_config.max_beta,
    )
    discount_rate = coe.rate
    clamp_note = (
        f" [dim](raw beta {coe.raw_beta:.2f}, clamped)[/dim]" if coe.was_clamped else ""
    )
    console.print(
        f"[bold]{ticker}[/bold] — price {inputs.current_price:,.2f} {inputs.currency}, "
        f"cost of equity {discount_rate:.2%} "
        f"(risk-free {risk_free_rate.value:.2%} + beta {coe.effective_beta:.2f}{clamp_note} "
        f"x ERP {valuation_config.equity_risk_premium:.2%})"
    )
    if inputs.currency != "USD":
        console.print(
            f"[yellow]note[/yellow] the risk-free rate is US 10Y Treasury (^TNX) — "
            f"there is no per-market rate wired up yet, so this cost of equity is a "
            f"rough approximation for a {inputs.currency}-denominated company, not a "
            f"{inputs.currency} market rate."
        )
    staleness_days = abs((risk_free_rate.as_of - inputs.as_of).days)
    if staleness_days > 3:
        console.print(
            f"[yellow]note[/yellow] price/fundamentals are as of {inputs.as_of} but the "
            f"risk-free rate is as of {risk_free_rate.as_of} — {staleness_days} days apart, "
            "mixing dates from different points in time."
        )
    if inputs.fcfe0 <= 0:
        console.print(
            f"[yellow]note[/yellow] {ticker}'s trailing free cash flow is negative "
            f"({inputs.fcfe0:,.0f} {inputs.currency}). Growing a negative base cash "
            "flow makes it MORE negative under positive growth, not less — this DCF's "
            "premise (a growing, cash-generative business) does not fit this company "
            "right now, and the value below should be read as that finding, not a "
            "literal negative price target."
        )

    if reverse:
        try:
            result = solve_implied_growth(
                current_price=inputs.current_price,
                fcfe0=inputs.fcfe0,
                shares_outstanding=inputs.shares_outstanding,
                terminal_growth=valuation_config.terminal_growth,
                cap_years=valuation_config.cap_years_default,
                discount_rate=discount_rate,
                min_growth=valuation_config.reverse_dcf_min_growth,
                max_growth=valuation_config.reverse_dcf_max_growth,
            )
        except (NoImpliedGrowthInRange, DcfInputError) as exc:
            console.print(f"[red]no implied growth solution[/red]: {exc}")
            raise typer.Exit(code=1) from None

        console.print(
            f"Implied growth: [bold]{result.implied_growth:+.2%}[/bold] "
            f"(fading to {result.terminal_growth:.2%} terminal over "
            f"{result.cap_years} years) — {result.margin_note}"
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
        sens_table = Table(title="Sensitivity — implied growth")
        sens_table.add_column("Discount rate")
        sens_table.add_column("Terminal growth")
        sens_table.add_column("Implied growth")
        for cell in cells:
            sens_table.add_row(
                f"{cell.discount_rate:.2%}",
                f"{cell.terminal_growth:.2%}",
                f"{cell.implied_growth:+.2%}" if cell.implied_growth is not None else "no solution",
            )
        console.print(sens_table)
        return

    if scenarios:
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
        except (ScenarioProbabilityError, ValuationInputError) as exc:
            console.print(f"[red]FAIL[/red] {ticker}: {exc}")
            raise typer.Exit(code=1) from None

        scenario_table = Table(title=f"{ticker} — bull/base/bear scenarios")
        scenario_table.add_column("Scenario")
        scenario_table.add_column("Probability")
        scenario_table.add_column("Growth")
        scenario_table.add_column("CAP years")
        scenario_table.add_column("Value/share")
        for r in analysis.results:
            value_display = (
                f"{r.value_per_share:,.2f}"
                if r.value_per_share is not None
                else f"undefined ({r.error})"
            )
            scenario_table.add_row(
                r.name,
                f"{r.probability:.0%}",
                f"{r.growth_start:+.2%}",
                str(r.cap_years),
                value_display,
            )
        console.print(scenario_table)
        if analysis.probability_weighted_value is not None:
            gap = (analysis.probability_weighted_value / inputs.current_price) - 1.0
            console.print(
                f"Probability-weighted value: [bold]{analysis.probability_weighted_value:,.2f}"
                f"[/bold] {inputs.currency} vs price {inputs.current_price:,.2f} ({gap:+.1%})"
            )
        else:
            console.print(
                "[yellow]Probability-weighted value undefined — "
                "see per-scenario errors above[/yellow]"
            )
        return

    # Default: plain forward DCF using the company's own trailing revenue
    # growth as a naive base-case input — explicitly disclosed as a starting
    # point pulled from trailing data, not an independent analyst forecast.
    try:
        assumptions = DcfAssumptions(
            fcfe0=inputs.fcfe0,
            shares_outstanding=inputs.shares_outstanding,
            growth_start=base_case_growth(inputs),
            terminal_growth=valuation_config.terminal_growth,
            cap_years=valuation_config.cap_years_default,
            discount_rate=discount_rate,
        )
        dcf_value = dcf_value_per_share(assumptions)
    except DcfInputError as exc:
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None

    gap = (dcf_value / inputs.current_price) - 1.0
    console.print(
        f"DCF value/share: [bold]{dcf_value:,.2f}[/bold] {inputs.currency} "
        f"(growth {assumptions.growth_start:+.2%} fading to "
        f"{assumptions.terminal_growth:.2%} over {assumptions.cap_years}y) "
        f"vs price {inputs.current_price:,.2f} ({gap:+.1%} gap)"
    )


def _parse_window(window: str) -> int:
    if not window.endswith("d"):
        raise typer.BadParameter(f"only day windows are supported, e.g. '30d' — got {window!r}")
    try:
        days = int(window[:-1])
    except ValueError as exc:
        raise typer.BadParameter(f"could not parse day count from {window!r}") from exc
    if days <= 0:
        raise typer.BadParameter(f"window must be positive, got {days}")
    return days


@app.command()
def why(
    ticker: str = typer.Argument(..., help="Ticker, e.g. AAPL"),  # noqa: B008
    window: str = typer.Option("30d", "--window", help="Attribution window, e.g. 30d."),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Decompose TICKER's return over --window into market/sector/residual,
    match the residual against real earnings events, and report the share of
    the move that could NOT be explained as an explicit headline number."""
    window_days = _parse_window(window)
    attribution_config = load_attribution_config()
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)

    region = resolve_market(ticker)
    market_index = attribution_config.market_index_by_region.get(region)
    if market_index is None:
        console.print(f"[red]FAIL[/red] {ticker}: no market index configured for region {region!r}")
        raise typer.Exit(code=1) from None

    lookback = attribution_config.lookback_months
    try:
        stock_history = yfin.get_price_history(ticker, months=lookback)
        market_history = yfin.get_price_history(market_index, months=lookback)
        snapshot = yfin.get_snapshot(ticker)
    except ProviderError as exc:
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None

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
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None

    try:
        earnings = yfin.get_earnings_history(ticker)
        matches = earnings_events_in_window(earnings.value, result.as_of, window_days)
    except ProviderError:
        matches = []
    confidence, confidence_reason = label_confidence(result.residual, matches)
    channel, channel_reason = classify_channel(result, confidence)

    console.print(
        f"[bold]{ticker}[/bold] — {window_days}d return {result.stock_return:+.2%} "
        f"as of {result.as_of}"
    )
    console.print(f"  alpha (drift, {result.n_observations} obs): {result.alpha_component:+.2%}")
    console.print(
        f"  market: beta {result.market_beta:.2f} x index return "
        f"{result.market_return:+.2%} = {result.market_component:+.2%}"
    )
    if result.sector_available:
        console.print(
            f"  sector: beta {result.sector_beta:.2f} x index return "
            f"{result.sector_return:+.2%} = {result.sector_component:+.2%}"
        )
    else:
        console.print(f"  sector: [dim]unavailable — {result.sector_unavailable_reason}[/dim]")
    console.print("  style: [dim]unavailable — needs a broader universe than this pilot has[/dim]")
    console.print(f"  residual: {result.residual:+.2%}")
    console.print(
        f"[bold]Unexplained: {result.unexplained_share:.0%} of the identified forces[/bold] "
        f"({confidence.value}: {confidence_reason})"
    )
    if result.r_squared < 0.3:
        console.print(
            f"[yellow]note[/yellow] the market/sector regression has low explanatory power "
            f"(R^2 {result.r_squared:.2f}) — treat the market/sector split above as a rough "
            "signal, not a precise one; most of the stock's variance is unrelated to the index."
        )
    console.print(
        "[dim]note the index used is not adjusted to exclude this stock — for a large index "
        "constituent this inflates the fitted beta and understates the residual "
        "(see attribution/decompose.py).[/dim]"
    )
    console.print(f"  channel: {channel.value} — {channel_reason}")
    if matches:
        console.print("  earnings events in window:")
        for m in matches:
            surprise = (
                f"{m.event.surprise_pct:+.1f}%"
                if m.event.surprise_pct is not None
                else "not yet reported"
            )
            console.print(f"    {m.event.as_of} — surprise {surprise} ({m.days_before_as_of}d ago)")
    if (
        confidence.value == "Unexplained"
        and abs(result.stock_return) >= attribution_config.large_unexplained_move_threshold
    ):
        console.print(
            f"[yellow]note[/yellow] a {abs(result.stock_return):.1%} move with no identified "
            "cause — flagged for attention, not explained away."
        )


_SIZEABLE_ACTIONS = {"strong_buy", "buy", "hold", "hold_trim"}


@app.command()
def decide(
    ticker: str = typer.Argument(..., help="Ticker, e.g. AAPL"),  # noqa: B008
    existing_position: bool = typer.Option(
        False, "--existing-position", help="Evaluate as an existing holding, not a new entry."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Full decision trace: Stage 1 gates, Stage 2 dual scores, Stage 3
    action with hysteresis, and position sizing."""
    decision_config = load_decision_rules()
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    stooq = StooqProvider(offline_fixtures_dir=fixtures_dir)
    cache = None if offline_fixtures else Cache(DEFAULT_CACHE_PATH)

    record = fetch_ticker(ticker, yfinance=yfin, stooq=stooq, base_currency="USD", cache=cache)
    if cache is not None:
        cache.close()

    try:
        peer_group = build_peer_group(ticker, yfin)
    except ProviderError as exc:
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None
    ctx = peer_group[ticker]
    info = ctx.snapshot.value

    price_conflict = next((c for c in record.quality.conflicts if c.field == "price"), None)
    # Found in review: the old expression reported 2 "agreeing" sources when
    # BOTH providers actually failed (price in missing_fields, not
    # single_sourced_fields) — a false PASS in exactly the case G0 exists to
    # catch. 0/1/2 now map onto missing/single-sourced/cross-checked.
    if "price" in record.quality.missing_fields:
        sources_agreeing = 0
    elif "price" in record.quality.single_sourced_fields:
        sources_agreeing = 1
    else:
        sources_agreeing = 2

    region = resolve_market(ticker)
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

    # Found in review: fundamentals_as_of was set from the snapshot's own
    # as_of, which comes from regularMarketTime — a PRICE-TICK timestamp,
    # not a filing date. That made the staleness check structurally unable
    # to ever fail. Use the real fundamentals dates yfinance reports instead.
    fundamentals_as_of = None
    most_recent_quarter = info.get("mostRecentQuarter")
    last_fiscal_year_end = info.get("lastFiscalYearEnd")
    for ts in (most_recent_quarter, last_fiscal_year_end):
        if ts is not None:
            fundamentals_as_of = datetime.fromtimestamp(ts, tz=UTC).date()
            break

    # Found in review: average_volume * price in the LISTING currency was
    # compared directly against a USD reference position with no FX
    # conversion — silently wrong for every non-USD ticker (e.g. treating a
    # JPY ADV figure as if it were already in dollars). Convert honestly, or
    # leave None rather than guess.
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
        "L",
        decision_config.scoring.long_horizon_weights,
        LONG_HORIZON_FACTOR_MAP,
        ctx,
        peer_group,
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

    console.print(f"[bold]{ticker}[/bold] — decision trace as of {ctx.as_of}")
    console.print()
    console.print("[bold]Stage 1 — Gates[/bold]")
    for result in gate_trace.results:
        marker = "[red]VETO[/red]" if result.veto else "[green]clear[/green]"
        console.print(f"  {result.gate}: {marker} — {result.reason}")
        for check in result.checks:
            symbol = {"pass": "+", "fail": "x", "not_evaluated": "?"}[check.status.value]
            # Rich parses `[x]` as markup and silently drops it — found in
            # review to swallow exactly the FAIL marker a human scans for.
            # Escape the brackets so they render as literal text.
            console.print(f"    \\[{symbol}] {check.name}: {check.detail}")
    console.print()
    console.print("[bold]Stage 2 — Scores[/bold]")
    for composite in (l_composite, s_composite):
        value_display = f"{composite.value:+.1f}" if composite.value is not None else "unavailable"
        coverage = f"{composite.weight_coverage:.0%}"
        console.print(f"  {composite.label}: {value_display} (weight coverage {coverage})")
        for c in composite.components:
            z_display = f"{c.directional_z:+.2f}" if c.directional_z is not None else "—"
            console.print(f"    {c.name} (w={c.weight:.2f}): {z_display} — {c.note}")
    conviction_display = f"{conviction:.2f}" if conviction is not None else "unavailable"
    console.print(f"  conviction (C): {conviction_display}")
    console.print()
    console.print("[bold]Stage 3 — Action[/bold]")
    console.print(f"  [bold]{action.value.upper()}[/bold] — {action_reason}")
    console.print(f"  timing overlay: {overlay}")

    if action.value in _SIZEABLE_ACTIONS:
        attribution_config = load_attribution_config()
        market_index = attribution_config.market_index_by_region.get(region)
        console.print()
        console.print("[bold]Sizing[/bold]")
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
            if not sizing.sizeable:
                console.print(f"  [yellow]cannot size[/yellow] — {sizing.unsizeable_reason}")
            else:
                vol_ratio_display = (
                    f"{sizing.vol_ratio:.2f}" if sizing.vol_ratio is not None else "n/a"
                )
                console.print(
                    f"  weight: {sizing.capped_weight_pct:.2f}% "
                    f"(raw {sizing.raw_weight_pct:.2f}%, vol_ratio {vol_ratio_display}, "
                    f"liquidity_cap {sizing.liquidity_cap:.2f})"
                )
                if sizing.binding_cap:
                    console.print(f"  [yellow]note[/yellow] capped by {sizing.binding_cap}")
        except ProviderError as exc:
            console.print(f"  [yellow]note[/yellow] sizing unavailable: {exc}")


@app.command()
def report(
    ticker: str = typer.Argument(..., help="Ticker, e.g. AAPL"),  # noqa: B008
    existing_position: bool = typer.Option(
        False, "--existing-position", help="Evaluate as an existing holding, not a new entry."
    ),
    window: str = typer.Option("30d", "--window", help="Attribution window, e.g. 30d."),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Write the JSON payload to this file instead of stdout."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Build the complete, self-contained JSON payload the reasoning layer
    reads (prompts/MASTER_ANALYSIS.md Part B) — identity, price, data
    quality, factor panel, valuation, attribution, and the full decision
    trace for TICKER. Prints nothing but the JSON to stdout unless
    --output is given, so it can be piped straight into a payload file."""
    window_days = _parse_window(window)
    try:
        payload = build_report_payload(
            ticker,
            existing_position=existing_position,
            window_days=window_days,
            offline_fixtures=offline_fixtures,
        )
    except ProviderError as exc:
        console.print(f"[red]FAIL[/red] {ticker}: {exc}")
        raise typer.Exit(code=1) from None

    # allow_nan=False: a NaN/Infinity anywhere in the payload would otherwise
    # serialise as a bare `NaN` token — valid to Python's own json.loads, but
    # rejected by JSON.parse/jq/every strict parser the reasoning layer or a
    # downstream tool might use. Fail loudly here rather than silently ship
    # a file that only THIS project's tests can read back.
    rendered = json.dumps(payload, indent=2, allow_nan=False)
    if output is not None:
        output.write_text(rendered)
        console.print(
            f"[bold]{ticker}[/bold] — payload written to {output} ({len(rendered)} bytes)"
        )
    else:
        print(rendered)


DEFAULT_PREDICTIONS_LOG_PATH = Path("data/predictions.jsonl")


@app.command(name="log-predictions")
def log_predictions_cmd(
    file: Path = typer.Argument(  # noqa: B008
        ..., help="JSON file: a single prediction object or a list of them."
    ),
) -> None:
    """Validate a batch of predictions against the seven-field contract
    (docs/01_PRICE_ACTION_FRAMEWORK.md §7) and append them to the
    append-only prediction log. All-or-nothing: if any prediction in the
    batch fails validation, none of them are logged."""
    predictions_config = load_predictions_config()
    try:
        raw = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]FAIL[/red] could not read {file}: {exc}")
        raise typer.Exit(code=1) from None

    raw_items = raw if isinstance(raw, list) else [raw]
    records = []
    parse_errors: list[str] = []
    for i, item in enumerate(raw_items):
        try:
            records.append(prediction_from_json(item))
        except (KeyError, ValueError, TypeError) as exc:
            parse_errors.append(f"item {i}: {exc}")
    if parse_errors:
        console.print(f"[red]FAIL[/red] {len(parse_errors)} malformed prediction(s), none logged:")
        for err in parse_errors:
            console.print(f"  {err}")
        raise typer.Exit(code=1)

    try:
        append_predictions(records, predictions_config.contract, DEFAULT_PREDICTIONS_LOG_PATH)
    except PredictionContractError as exc:
        console.print(f"[red]FAIL[/red] contract validation failed, none logged: {exc}")
        raise typer.Exit(code=1) from None

    console.print(
        f"[bold]logged {len(records)} prediction(s)[/bold] to {DEFAULT_PREDICTIONS_LOG_PATH}"
    )


@app.command(name="score-predictions")
def score_predictions_cmd(
    as_of: str | None = typer.Option(
        None, "--as-of", help="Resolve as of this date (YYYY-MM-DD). Defaults to today."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Resolve every elapsed prediction in the log against real price data
    and print hit rate, magnitude error, and Brier score — overall, by
    factor, and by horizon (docs/01_PRICE_ACTION_FRAMEWORK.md §7 /
    CLAUDE.md §3.5 rule 16)."""
    predictions_config = load_predictions_config()
    records = read_predictions(DEFAULT_PREDICTIONS_LOG_PATH)
    if not records:
        console.print(
            f"[yellow]no predictions logged yet[/yellow] at {DEFAULT_PREDICTIONS_LOG_PATH}"
        )
        return

    as_of_date = date.fromisoformat(as_of) if as_of else datetime.now(tz=UTC).date()
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
    lookup = yfinance_price_lookup(yfin, as_of_date, predictions_config.resolution)
    report = score_predictions(records, as_of_date, lookup)

    def _row(table: Table, bucket_label: str, bucket: ScoreBucket) -> None:
        table.add_row(
            bucket_label,
            str(bucket.n),
            f"{bucket.hit_rate:.0%}" if bucket.hit_rate is not None else "—",
            f"{bucket.mean_magnitude_error:.2%}"
            if bucket.mean_magnitude_error is not None
            else "—",
            f"{bucket.brier_score:.3f}" if bucket.brier_score is not None else "—",
        )

    table = Table(title=f"Prediction calibration — as of {as_of_date}")
    table.add_column("Bucket")
    table.add_column("n")
    table.add_column("Hit rate")
    table.add_column("Mean magnitude error")
    table.add_column("Brier score")
    _row(table, "overall", report.overall)
    for label in sorted(report.by_horizon):
        _row(table, f"horizon: {label}", report.by_horizon[label])
    for label in sorted(report.by_factor):
        _row(table, f"factor: {label}", report.by_factor[label])
    console.print(table)

    console.print(
        f"pending (horizon not yet elapsed): {len(report.pending)}  |  "
        f"unresolved (elapsed, no price data): {len(report.unresolved)}"
    )
    for record, reason in report.unresolved:
        console.print(f"  [yellow]note[/yellow] {record.id} ({record.ticker}): {reason}")


@app.command()
def backtest(
    tickers: list[str] | None = typer.Argument(  # noqa: B008
        None, help="Tickers to backtest. Defaults to config/universe.yaml's pilot universe."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Walk-forward backtest of a price-momentum signal, net of realistic
    transaction costs, with the Deflated Sharpe Ratio correcting for the
    number of configurations swept (docs/01_PRICE_ACTION_FRAMEWORK.md §8).
    See backtest/engine.py's module docstring for this milestone's honestly
    disclosed scope — a price-only signal, not the full gate/score decision
    tree, which needs a point-in-time fundamentals store this project does
    not have yet."""
    backtest_config = load_backtest_config()
    wf = backtest_config.walk_forward
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)

    universe_raw = yaml.safe_load(Path("config/universe.yaml").read_text())
    if not tickers:
        tickers = list(universe_raw.get("tickers", {}).keys())
    base_currency = universe_raw.get("base_currency", "USD")
    periods_per_year = 365.0 / wf.rebalance_step_days

    configs = [
        MomentumConfig(
            label=f"{d}d",
            formation_days=d,
            skip_days=wf.momentum_skip_days,
            price_match_tolerance_days=wf.price_match_tolerance_days,
            vol_lookback_days=wf.vol_regime_lookback_days,
        )
        for d in wf.momentum_formation_days_grid
    ]
    n_trials = len(configs)

    for ticker in tickers:
        try:
            history = yfin.get_price_history(ticker)
            snapshot = yfin.get_snapshot(ticker)
        except ProviderError as exc:
            console.print(f"[red]FAIL[/red] {ticker}: {exc}")
            continue

        series = PointInTimeSeries(ticker, history.value)
        region = resolve_market(ticker)
        ad_hoc_ctx = FactorContext(
            ticker=ticker, as_of=snapshot.as_of, snapshot=snapshot, price_history=None
        )
        quote_currency = snapshot.value.get("currency")
        is_cross_currency = quote_currency is not None and quote_currency != base_currency

        # Found in quant-reviewer audit: average_volume * latest_close is in
        # the ticker's LISTING currency, not automatically base_currency —
        # multiplying it straight into a USD-denominated notional_trade_usd
        # silently mispriced market impact for every non-USD pilot ticker
        # (understated ~19-21bps for 7203.T/1299.HK). Same bug class already
        # fixed once for the `decide` command's ADV liquidity check above —
        # convert via the FX rate as of the price date, or leave None.
        average_volume = snapshot_field(ad_hoc_ctx, "averageVolume")
        latest_close = history.value[-1].close if history.value else None
        average_daily_dollar_volume = None
        if average_volume is not None and latest_close is not None and quote_currency is not None:
            native_adv = average_volume * latest_close
            if quote_currency == base_currency:
                average_daily_dollar_volume = native_adv
            else:
                try:
                    fx = yfin.get_fx_rate(quote_currency, base_currency)
                    average_daily_dollar_volume = native_adv * fx.value
                except ProviderError:
                    average_daily_dollar_volume = None

        trailing_dividend_yield = snapshot_field(ad_hoc_ctx, "trailingAnnualDividendYield")

        console.print(
            f"\n[bold]{ticker}[/bold] — walk-forward momentum backtest, {n_trials} configs"
        )
        table = Table()
        table.add_column("Config")
        table.add_column("n trades")
        table.add_column("Gross Sharpe")
        table.add_column("Net Sharpe")

        best_label: str | None = None
        best_net_returns: list[float] = []
        best_net_sharpe = float("-inf")
        best_steps: list[RebalanceStep] = []

        for cfg in configs:
            steps = run_walk_forward(series, cfg, wf.rebalance_step_days)
            traded = [s for s in steps if s.position != 0 and s.forward_return is not None]
            gross_returns = [s.forward_return for s in traded if s.forward_return is not None]

            net_returns: list[float] = []
            for s in traded:
                assert s.forward_return is not None
                try:
                    costs = round_trip_costs(
                        backtest_config.costs,
                        market=region,
                        position=s.position,
                        holding_period_days=wf.rebalance_step_days,
                        trade_value=backtest_config.costs.notional_trade_usd,
                        average_daily_dollar_volume=average_daily_dollar_volume,
                        is_cross_currency=is_cross_currency,
                        trailing_dividend_yield=trailing_dividend_yield,
                    )
                except ValueError:
                    continue
                net_returns.append(s.forward_return - costs.total_bps / 10_000.0)

            gross_sharpe = sharpe_ratio(gross_returns, periods_per_year)
            net_sharpe = sharpe_ratio(net_returns, periods_per_year)
            table.add_row(
                cfg.label,
                str(len(net_returns)),
                f"{gross_sharpe:.2f}" if gross_sharpe is not None else "—",
                f"{net_sharpe:.2f}" if net_sharpe is not None else "—",
            )
            if net_sharpe is not None and net_sharpe > best_net_sharpe:
                best_net_sharpe = net_sharpe
                best_label = cfg.label
                best_net_returns = net_returns
                best_steps = traded
        console.print(table)

        if best_label is None:
            console.print("  [yellow]no config produced a scoreable return series[/yellow]")
            continue

        dsr = deflated_sharpe_ratio(best_net_returns, periods_per_year, n_trials)
        if dsr is None:
            console.print(
                f"  best config: {best_label} — too few net trades to compute a "
                "Deflated Sharpe Ratio"
            )
        else:
            console.print(
                f"  best config: {best_label}  |  "
                f"raw net Sharpe {dsr.observed_sharpe_annualised:.2f}  |  "
                f"Deflated Sharpe Ratio {dsr.deflated_sharpe:.3f} "
                f"(P[true Sharpe > 0], corrected for {n_trials} configs tried)"
            )

        vols = [s.trailing_realized_vol for s in best_steps]
        rets = [s.forward_return for s in best_steps if s.forward_return is not None]
        if len(rets) == len(vols) and rets:
            regime = split_by_volatility_regime(rets, vols, periods_per_year)
            for label, bucket in regime.items():
                sharpe_display = f"{bucket.sharpe:.2f}" if bucket.sharpe is not None else "—"
                console.print(f"  regime {label}: n={bucket.n}, Sharpe={sharpe_display}")
        console.print(
            "  [dim]note: 2008/2020/2022-style stress-window splits are out of range for "
            "this project's fetched price history — see backtest/metrics.py[/dim]"
        )


@app.command()
def screen(
    universe: Path = typer.Option(  # noqa: B008
        Path("config/universe.yaml"), "--universe", help="Universe YAML file listing tickers."
    ),
    offline_fixtures: bool = typer.Option(
        False, "--offline-fixtures", help="Read from recorded fixtures instead of live network."
    ),
) -> None:
    """Screen a universe for data-quality gaps and reverse-DCF valuation
    extremes. Cache-first and rate-limit-respecting across the WHOLE run:
    one shared Cache and one shared, config-driven rate limiter per
    provider are constructed ONCE before the ticker loop (not per ticker),
    so pacing genuinely persists across the batch instead of resetting.
    Core fetch fields (price/currency/shares/eps/name) are served through
    the SQLite cache exactly like `analyze fetch`; valuation reads share
    the same rate limiter for the whole run but are not independently
    cached yet — a disclosed limitation, not a silent gap.

    This project has never implemented an Alpha Vantage provider (its
    ~25 req/day cap is the sharpest version of the fan-out risk this
    command is designed against) — config/providers.yaml has no entry for
    it, and the same shared-limiter + cache-first discipline here is what
    would protect it too if one were added."""
    providers_config = load_providers_config()
    valuation_config = load_valuation_config()
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None
    yfin = YFinanceProvider(
        offline_fixtures_dir=fixtures_dir,
        rate_limiter=providers_config.rate_limits["yfinance"].build_token_bucket(),
    )
    stooq = StooqProvider(
        offline_fixtures_dir=fixtures_dir,
        rate_limiter=providers_config.rate_limits["stooq"].build_token_bucket(),
    )
    cache = None if offline_fixtures else Cache(DEFAULT_CACHE_PATH)

    universe_raw = yaml.safe_load(universe.read_text())
    tickers = list(universe_raw.get("tickers", {}).keys())
    base_currency = universe_raw.get("base_currency", "USD")
    # `.get("screening", {})` only substitutes the default when the key is
    # ABSENT — found in review that a `screening:` header left with an empty
    # body parses as `None`, not `{}`, and `None.get(...)` raised a raw
    # AttributeError before a single ticker was processed. `or {}` catches
    # both "key absent" and "key present but empty."
    screening_raw = universe_raw.get("screening") or {}
    raw_threshold = screening_raw.get("large_implied_growth_gap", 0.15)
    try:
        large_gap_threshold = float(raw_threshold)
        if large_gap_threshold <= 0:
            raise ValueError
    except (TypeError, ValueError):
        console.print(
            f"[red]FAIL[/red] config/universe.yaml's screening.large_implied_growth_gap "
            f"must be a positive number, got {raw_threshold!r}"
        )
        raise typer.Exit(code=1) from None

    table = Table(title=f"analyze screen — {len(tickers)} tickers, {universe}")
    table.add_column("Ticker")
    table.add_column("Region")
    table.add_column("Completeness")
    table.add_column("Implied growth")
    table.add_column("Trailing growth")
    table.add_column("Gap")
    table.add_column("Note")

    # The risk-free rate (^TNX) is ticker-independent — found in review that
    # fetching it fresh inside the per-ticker loop was wasteful and, for a
    # genuinely rate-capped provider, exactly the redundant-request pressure
    # this milestone is meant to guard against. One fetch for the whole run.
    risk_free_rate_value: float | None = None
    try:
        risk_free_rate_value = yfin.get_risk_free_rate().value
    except ProviderError as exc:
        console.print(f"[yellow]note[/yellow] risk-free rate unavailable: {exc}")

    try:
        for ticker in tickers:
            record = fetch_ticker(
                ticker, yfinance=yfin, stooq=stooq, base_currency=base_currency, cache=cache
            )
            region = resolve_market(ticker)
            implied_display, trailing_display, gap_display, note = "—", "—", "—", ""
            if risk_free_rate_value is None:
                note = "risk-free rate unavailable"
                table.add_row(
                    ticker,
                    region,
                    f"{record.quality.completeness:.0%}",
                    implied_display,
                    trailing_display,
                    gap_display,
                    note,
                )
                continue
            try:
                inputs = gather_valuation_inputs(ticker, yfin)
                coe = cost_of_equity(
                    risk_free_rate=risk_free_rate_value,
                    beta=inputs.beta,
                    equity_risk_premium=valuation_config.equity_risk_premium,
                    min_beta=valuation_config.min_beta,
                    max_beta=valuation_config.max_beta,
                )
                reverse = solve_implied_growth(
                    current_price=inputs.current_price,
                    fcfe0=inputs.fcfe0,
                    shares_outstanding=inputs.shares_outstanding,
                    terminal_growth=valuation_config.terminal_growth,
                    cap_years=valuation_config.cap_years_default,
                    discount_rate=coe.rate,
                    min_growth=valuation_config.reverse_dcf_min_growth,
                    max_growth=valuation_config.reverse_dcf_max_growth,
                )
                implied_display = f"{reverse.implied_growth:+.1%}"
                if inputs.trailing_revenue_growth is not None:
                    trailing_display = f"{inputs.trailing_revenue_growth:+.1%}"
                    gap = reverse.implied_growth - inputs.trailing_revenue_growth
                    gap_display = f"{gap:+.1%}"
                    if abs(gap) >= large_gap_threshold:
                        note = "[yellow]large implied-growth gap[/yellow]"
                else:
                    note = "no trailing growth to compare against"
            except (
                ValuationInputError,
                ProviderError,
                NoImpliedGrowthInRange,
                DcfInputError,
            ) as exc:
                reason = str(exc)
                truncated = reason if len(reason) <= 70 else reason[:67] + "..."
                note = f"valuation unavailable: {truncated}"

            table.add_row(
                ticker,
                region,
                f"{record.quality.completeness:.0%}",
                implied_display,
                trailing_display,
                gap_display,
                note,
            )
    finally:
        if cache is not None:
            cache.close()

    console.print(table)


if __name__ == "__main__":
    app()
