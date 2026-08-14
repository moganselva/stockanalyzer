"""Stock Analyzer CLI. M1: `analyze fetch`. M2: `analyze factors`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import factors as _factors  # noqa: F401  (side effect: registers every factor)
from .attribution.channel import classify_channel
from .attribution.config import load_attribution_config
from .attribution.decompose import InsufficientDataError, decompose
from .attribution.events import earnings_events_in_window, label_confidence
from .data.base import ProviderError
from .data.cache import Cache
from .data.fetch import fetch_ticker
from .data.normalize import resolve_market
from .data.providers.stooq_provider import StooqProvider
from .data.providers.yfinance_provider import YFinanceProvider
from .factors.peers import build_peer_group
from .factors.registry import (
    compute_factor,
    load_factor_config,
    validate_registry_matches_config,
    winsorized_zscore,
)
from .valuation.config import load_valuation_config
from .valuation.dcf import DcfAssumptions, DcfInputError, cost_of_equity, dcf_value_per_share
from .valuation.inputs import ValuationInputError, ValuationInputs, gather_valuation_inputs
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
                base_growth=_base_case_growth(ticker, inputs),
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
            growth_start=_base_case_growth(ticker, inputs),
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


def _base_case_growth(ticker: str, inputs: ValuationInputs) -> float:
    """The forward DCF's base-case growth: the company's own trailing revenue
    growth, taken as-is with no forecasting judgement applied — a disclosed
    starting point pulled from trailing data, not an independent analyst
    forecast. `analyze value --reverse` is the module actually meant to be
    trusted for what the market is pricing in; this default view exists so a
    plain `analyze value TICKER` still produces a real number to react to."""
    if inputs.trailing_revenue_growth is None:
        raise ValuationInputError(f"{ticker}: no trailing revenue growth available for a base case")
    return inputs.trailing_revenue_growth


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


if __name__ == "__main__":
    app()
