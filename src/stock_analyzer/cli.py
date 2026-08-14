"""Stock Analyzer CLI. M1 scope: `analyze fetch`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .data.cache import Cache
from .data.fetch import fetch_ticker
from .data.providers.stooq_provider import StooqProvider
from .data.providers.yfinance_provider import YFinanceProvider

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


if __name__ == "__main__":
    app()
