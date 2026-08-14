from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

MILESTONE_TICKERS = ["AAPL", "7203.T", "ASML.AS", "1299.HK"]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
