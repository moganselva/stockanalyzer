"""Loads config/providers.yaml. CLAUDE.md §3.1 rule 5: "All providers go
through a token-bucket rate limiter" — this is what makes that rate config
real rather than a duplicated, unenforced number sitting next to the actual
hardcoded default in each provider's constructor. Found while building M9's
`analyze screen`: config/providers.yaml was never loaded anywhere in the
codebase — every provider's TokenBucket used its own hardcoded default,
which happened to match the file's numbers by coincidence, not by
construction. Screening a whole universe is the first command that
genuinely exercises many requests in one run, which is exactly where an
un-enforced rate-limit config would first bite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .ratelimit import TokenBucket

DEFAULT_CONFIG_PATH = Path("config/providers.yaml")


@dataclass(frozen=True)
class RateLimitSetting:
    requests_per_second: float
    burst: int

    def build_token_bucket(self) -> TokenBucket:
        return TokenBucket(rate_per_second=self.requests_per_second, capacity=self.burst)


@dataclass(frozen=True)
class ProvidersConfig:
    rate_limits: dict[str, RateLimitSetting]


def load_providers_config(path: Path = DEFAULT_CONFIG_PATH) -> ProvidersConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    rate_limits = raw["rate_limits"]
    return ProvidersConfig(
        rate_limits={
            name: RateLimitSetting(
                requests_per_second=float(settings["requests_per_second"]),
                burst=int(settings["burst"]),
            )
            for name, settings in rate_limits.items()
        }
    )
