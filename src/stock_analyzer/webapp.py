"""On-demand ticker lookup: a small live server wrapping the same
report/builder.py + report/dashboard.py logic the static `analyze dashboard`
export uses. Unlike that export, a ticker here is not pre-baked or drawn from
config/universe.yaml — any symbol yfinance recognises can be looked up. Peer
relative z-scores still only compute for tickers whose (sector, region)
bucket has real peers in config/universe.yaml; everything else stays a
real None, per CLAUDE.md's "missing is missing" rule, same as the CLI.

Run locally with `analyze serve`. Not deployed anywhere by default — see
CLAUDE.md §7 on free-tier rate limits before pointing this at real traffic.
"""

from __future__ import annotations

from html import escape as _esc

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .data.base import ProviderError
from .data.providers.yfinance_provider import YFinanceProvider
from .report.builder import DEFAULT_FIXTURES_DIR, build_report_payload
from .report.dashboard import TickerDashboardData, render_dashboard_html

_SEARCH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Stock Analyzer — lookup</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #f6f8fa;
          color: #1f2328; display: flex; align-items: center; justify-content: center;
          height: 100vh; margin: 0; }}
  form {{ background: white; border: 1px solid #d0d7de; border-radius: 8px; padding: 32px;
          text-align: center; min-width: 320px; }}
  h1 {{ margin: 0 0 18px 0; font-size: 18px; }}
  input {{ font-size: 16px; padding: 8px 12px; border: 1px solid #d0d7de; border-radius: 6px;
           width: 200px; text-transform: uppercase; }}
  button {{ font-size: 15px; padding: 9px 18px; margin-left: 8px; background: #2f81f7;
            color: white; border: none; border-radius: 6px; cursor: pointer; }}
  .error {{ color: #cf222e; margin-top: 14px; font-size: 14px; }}
</style>
</head>
<body>
<form action="/lookup" method="get">
  <h1>Stock Analyzer — look up any ticker</h1>
  <input name="ticker" placeholder="e.g. AAPL, 7203.T" autofocus value="{prefill}">
  <button type="submit">Look up</button>
  {error_html}
</form>
</body>
</html>"""


def create_app(offline_fixtures: bool = False) -> FastAPI:
    """`offline_fixtures=True` is only ever set by tests — production and the
    `analyze serve` CLI command both leave it False, i.e. live data."""
    app = FastAPI(title="Stock Analyzer")
    fixtures_dir = DEFAULT_FIXTURES_DIR if offline_fixtures else None

    @app.get("/", response_class=HTMLResponse)
    def search_form() -> str:
        return _SEARCH_PAGE.format(prefill="", error_html="")

    @app.get("/lookup", response_class=HTMLResponse)
    def lookup(ticker: str = "") -> HTMLResponse:
        ticker = ticker.strip().upper()
        if not ticker:
            return HTMLResponse(_SEARCH_PAGE.format(prefill="", error_html=""))

        yfin = YFinanceProvider(offline_fixtures_dir=fixtures_dir)
        try:
            payload = build_report_payload(ticker, offline_fixtures=offline_fixtures)
        except (ProviderError, ValueError) as exc:
            # This is a system boundary (arbitrary user-typed input), unlike
            # the rest of this codebase's narrow exception handling — a
            # malformed/unknown ticker must show an error page, not crash
            # the server.
            error_html = (
                f'<p class="error">Could not fetch data for '
                f"{_esc(ticker)}: {_esc(str(exc))}</p>"
            )
            return HTMLResponse(
                _SEARCH_PAGE.format(prefill=_esc(ticker), error_html=error_html), status_code=404
            )

        try:
            price_points = yfin.get_price_history(ticker).value
        except ProviderError:
            price_points = None

        doc = render_dashboard_html(
            tickers=[TickerDashboardData(ticker=ticker, payload=payload, price_points=price_points)]
        )
        return HTMLResponse(doc)

    return app


app = create_app()
