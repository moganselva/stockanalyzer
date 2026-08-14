"""Stock Analyzer CLI. M1: `analyze fetch`. M2: `analyze factors`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import factors as _factors  # noqa: F401  (side effect: registers every factor)
from .data.cache import Cache
from .data.fetch import fetch_ticker
from .data.providers.stooq_provider import StooqProvider
from .data.providers.yfinance_provider import YFinanceProvider
from .factors.peers import build_peer_group
from .factors.registry import (
    compute_factor,
    load_factor_config,
    validate_registry_matches_config,
    winsorized_zscore,
)

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


if __name__ == "__main__":
    app()
