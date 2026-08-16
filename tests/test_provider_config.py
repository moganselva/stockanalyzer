"""data/provider_config.py — makes config/providers.yaml's rate limits real.
CLAUDE.md §3.1 rule 5: "All providers go through a token-bucket rate
limiter." MILESTONES.md M9: "screening a universe respects every provider
rate limit with a cache-first access pattern — no fan-out that would
exhaust the Alpha Vantage daily cap."
"""

from __future__ import annotations

import pytest

from stock_analyzer.data.provider_config import RateLimitSetting, load_providers_config
from stock_analyzer.data.ratelimit import TokenBucket


def test_real_config_file_loads_yfinance_and_stooq() -> None:
    config = load_providers_config()
    assert "yfinance" in config.rate_limits
    assert "stooq" in config.rate_limits
    assert config.rate_limits["yfinance"].requests_per_second > 0
    assert config.rate_limits["yfinance"].burst > 0
    assert config.rate_limits["stooq"].requests_per_second > 0


def test_rate_limit_setting_builds_a_working_token_bucket() -> None:
    setting = RateLimitSetting(requests_per_second=5.0, burst=3)
    bucket = setting.build_token_bucket()
    assert isinstance(bucket, TokenBucket)
    # a fresh bucket at full capacity must not block on the first acquire
    bucket.acquire()


def test_token_bucket_state_persists_across_acquisitions() -> None:
    """The property that actually matters for M9: a SHARED bucket's token
    count genuinely decreases as it is reused, proving pacing state
    persists across calls rather than resetting."""
    bucket = TokenBucket(rate_per_second=0.001, capacity=3)  # effectively no refill in test time
    bucket.acquire()
    bucket.acquire()
    bucket.acquire()
    assert bucket._tokens < 1.0  # noqa: SLF001


def test_a_fresh_bucket_per_call_would_not_pace_a_batch() -> None:
    """Demonstrates exactly why `analyze screen` must construct ONE
    provider (and therefore one TokenBucket) before its ticker loop rather
    than one per ticker: a brand-new bucket always starts at full capacity
    with no memory of any other bucket's usage, so N fresh buckets could
    issue N*capacity requests with zero pacing between them — the "fan-out"
    MILESTONES.md M9 explicitly warns against."""
    shared = TokenBucket(rate_per_second=1.0, capacity=2)
    shared.acquire()
    shared.acquire()
    assert shared._tokens < 1.0  # noqa: SLF001

    fresh = TokenBucket(rate_per_second=1.0, capacity=2)
    assert fresh._tokens == 2.0  # noqa: SLF001


def test_token_bucket_rejects_non_positive_parameters() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(rate_per_second=0.0, capacity=5)
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(rate_per_second=1.0, capacity=0)
