"""webapp.py — on-demand ticker lookup server. TestClient talks to the ASGI
app in-process (no real socket), so this stays offline per CLAUDE.md §3.3
rule 11; offline_fixtures=True keeps the underlying data layer offline too.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from stock_analyzer.webapp import create_app


def _client() -> TestClient:
    return TestClient(create_app(offline_fixtures=True))


def test_search_form_renders_at_root() -> None:
    resp = _client().get("/")
    assert resp.status_code == 200
    assert "<form" in resp.text
    assert "Look up" in resp.text


def test_lookup_with_no_ticker_redisplays_search_form() -> None:
    resp = _client().get("/lookup", params={"ticker": ""})
    assert resp.status_code == 200
    assert "<form" in resp.text


def test_lookup_known_ticker_renders_full_dashboard() -> None:
    resp = _client().get("/lookup", params={"ticker": "AAPL"})
    assert resp.status_code == 200
    assert resp.text.startswith("<!DOCTYPE html>")
    assert "Apple" in resp.text or "AAPL" in resp.text


def test_lookup_is_case_and_whitespace_insensitive() -> None:
    resp = _client().get("/lookup", params={"ticker": "  aapl  "})
    assert resp.status_code == 200
    assert "AAPL" in resp.text


def test_lookup_unknown_ticker_shows_error_not_a_crash() -> None:
    resp = _client().get("/lookup", params={"ticker": "NOSUCHFIXTURE"})
    assert resp.status_code == 404
    assert "Could not fetch data" in resp.text
    assert "<form" in resp.text  # user can immediately try another symbol


def test_lookup_error_page_escapes_the_ticker() -> None:
    """The ticker is user-typed input reflected back into HTML — must be
    escaped, not a stored-XSS vector."""
    resp = _client().get("/lookup", params={"ticker": "<script>alert(1)</script>"})
    assert resp.status_code == 404
    assert "<script>" not in resp.text.lower()
    assert "&lt;script&gt;" in resp.text.lower()
