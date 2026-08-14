"""Stage 1 — hard gates. docs/01_PRICE_ACTION_FRAMEWORK.md §6.

"Gates run first and are non-negotiable — no score can override a failed
gate." Every check is tri-state (PASS / FAIL / NOT_EVALUATED), not binary:
CLAUDE.md §3.1 rule 4 forbids treating a missing input as if it were a
value, and this project's free-tier data genuinely cannot compute several of
the framework's named checks (Beneish M-Score, Altman Z, DSO/DIO growth,
governance-veto triggers — none need a data source this project has wired
up). A real FAIL always vetoes. A NOT_EVALUATED check never vetoes on its
own — but it is never silently dropped either: it is named in the trace and
lowers conviction (GateTrace.data_completeness), which is exactly what
CLAUDE.md's honesty rules ask for: say what you don't know, never assume it
would have passed.

Each GateCheck also declares `evaluable`: whether this specific check has
ANY code path in this project that could ever produce PASS/FAIL, for ANY
ticker (e.g. `ocf_to_ni` does — it depends on data availability per
company), versus checks with zero implementation at all right now
(`beneish_m_score`, `altman_z`, `governance_veto_triggers`, ...). Found in
review: folding both kinds into one completeness denominator meant
conviction was permanently capped around 0.42 regardless of how good a
ticker's actual, checkable data was — 8 of 13 checks have no implementation
at all and never will until a real data source is wired up, and diluting
completeness with checks that were NEVER going to be available conflates
"this ticker has thin data" with "this system doesn't have this feature
yet". `data_completeness` is computed only over `evaluable=True` checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from .config import DecisionRulesConfig


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: GateStatus
    detail: str
    evaluable: bool = True


@dataclass(frozen=True)
class GateResult:
    gate: str
    checks: list[GateCheck]
    veto: bool
    reason: str

    @property
    def evaluated_fraction(self) -> float:
        evaluable_checks = [c for c in self.checks if c.evaluable]
        if not evaluable_checks:
            return 0.0
        evaluated = sum(1 for c in evaluable_checks if c.status != GateStatus.NOT_EVALUATED)
        return evaluated / len(evaluable_checks)


def _unimplemented(name: str) -> GateCheck:
    """A check with literally no code path to evaluate it for any ticker —
    excluded from the completeness denominator, see module docstring."""
    return GateCheck(
        name,
        GateStatus.NOT_EVALUATED,
        "no data source wired up for this check yet",
        evaluable=False,
    )


@dataclass(frozen=True)
class GateContext:
    ticker: str
    now: date

    # G0 — data integrity (real: from M1's fetch/QualityReport)
    sources_agreeing: int
    price_disagreement_pct: float | None  # a FRACTION (0.02 = 2%), matching quality.py's pct_spread
    fundamentals_as_of: date | None  # a real filing/estimate date, never a price-tick timestamp
    currency_resolved: bool

    # G1 — accounting quality (partial: trailing OCF/NI is a real ratio, but
    # not the framework's specified 3-yr average — no point-in-time
    # fundamentals history exists yet to compute a true multi-year average)
    ocf_to_ni_trailing: float | None

    # G2 — solvency (real: from snapshot fields)
    net_debt: float | None
    ebitda: float | None
    sector: str | None  # GICS-style sector string, for the sector-specific threshold

    # G3 — investability. average_daily_dollar_volume must already be
    # converted to the SAME currency as reference_position (both USD) —
    # found in review that multiplying native-currency average_volume by a
    # native-currency price and comparing directly to a USD position size
    # was silently wrong for every non-USD ticker.
    average_daily_dollar_volume: float | None
    market_accessible: bool | None
    reference_position_usd: float


def check_g0_data_integrity(ctx: GateContext, config: DecisionRulesConfig) -> GateResult:
    cfg = config.gates.data_integrity
    checks: list[GateCheck] = []

    sources_ok = ctx.sources_agreeing >= cfg.min_sources_agreeing
    checks.append(
        GateCheck(
            "sources_agreeing",
            GateStatus.PASS if sources_ok else GateStatus.FAIL,
            f"{ctx.sources_agreeing} source(s) agreeing, need >= {cfg.min_sources_agreeing}",
        )
    )

    if ctx.price_disagreement_pct is None:
        checks.append(
            GateCheck("price_disagreement", GateStatus.NOT_EVALUATED, "only one price source")
        )
    else:
        # Both sides are fractions (0.02 = 2%) — config's *_pct name is a
        # percent NUMBER (2.0 meaning 2%), so it's divided by 100 here.
        # Found in review: comparing the raw fraction against the raw
        # percent number made the effective tolerance 200%, not 2%.
        tolerance = cfg.max_price_disagreement_pct / 100.0
        ok = ctx.price_disagreement_pct <= tolerance
        checks.append(
            GateCheck(
                "price_disagreement",
                GateStatus.PASS if ok else GateStatus.FAIL,
                f"{ctx.price_disagreement_pct:.2%} disagreement, max {tolerance:.2%}",
            )
        )

    if ctx.fundamentals_as_of is None:
        checks.append(
            GateCheck("fundamentals_staleness", GateStatus.NOT_EVALUATED, "no fundamentals date")
        )
    else:
        age_days = (ctx.now - ctx.fundamentals_as_of).days
        ok = age_days <= cfg.max_fundamentals_age_days
        checks.append(
            GateCheck(
                "fundamentals_staleness",
                GateStatus.PASS if ok else GateStatus.FAIL,
                f"{age_days}d old, max {cfg.max_fundamentals_age_days}d",
            )
        )

    checks.append(
        GateCheck(
            "currency_resolved",
            GateStatus.PASS if ctx.currency_resolved else GateStatus.FAIL,
            "currency resolved" if ctx.currency_resolved else "currency not resolved",
        )
    )
    # No ADR-ratio / share-class resolver exists anywhere in this project —
    # found in review that deriving this from currency_resolved fabricated a
    # PASS with nothing behind it. Honestly unimplemented, like G1/G2/G4's
    # unwired checks.
    checks.append(_unimplemented("share_class_resolved"))

    veto = any(c.status == GateStatus.FAIL for c in checks)
    return GateResult(
        gate="G0_data_integrity",
        checks=checks,
        veto=veto,
        reason="a data integrity check failed" if veto else "all evaluated checks passed",
    )


def check_g1_accounting_quality(ctx: GateContext, config: DecisionRulesConfig) -> GateResult:
    cfg = config.gates.accounting_quality
    checks: list[GateCheck] = []

    if ctx.ocf_to_ni_trailing is None:
        checks.append(
            GateCheck(
                "ocf_to_ni",
                GateStatus.NOT_EVALUATED,
                "operating cash flow or net income unavailable",
            )
        )
    else:
        ok = ctx.ocf_to_ni_trailing >= cfg.min_ocf_to_ni_3yr_avg
        checks.append(
            GateCheck(
                "ocf_to_ni",
                GateStatus.PASS if ok else GateStatus.FAIL,
                f"trailing OCF/NI {ctx.ocf_to_ni_trailing:.2f} (proxy for the framework's 3-yr "
                f"average, which needs point-in-time history this project does not have yet), "
                f"min {cfg.min_ocf_to_ni_3yr_avg:.2f}",
            )
        )

    for name in (
        "accrual_ratio_percentile",
        "dso_dio_growth_vs_revenue",
        "gaap_vs_adjusted_gap",
        "beneish_m_score",
    ):
        checks.append(_unimplemented(name))

    flags = sum(1 for c in checks if c.status == GateStatus.FAIL)
    veto = flags >= cfg.max_flags
    return GateResult(
        gate="G1_accounting_quality",
        checks=checks,
        veto=veto,
        reason=f"{flags} flag(s), veto at >= {cfg.max_flags}" if veto else f"{flags} flag(s)",
    )


# GICS-style sector string -> decision_rules.yaml solvency threshold bucket.
# Anything not listed here uses "default". A bucket mapped to None (financials
# in config today) means the check is not meaningful for that sector at all
# (a bank's balance sheet structure makes net-debt/EBITDA the wrong lens) —
# reported NOT_EVALUATED, not silently measured against an unrelated default.
_SOLVENCY_SECTOR_BUCKETS: dict[str, str] = {
    "Utilities": "utilities",
    "Financial Services": "financials",
    "Real Estate": "reits",
}


def check_g2_solvency(ctx: GateContext, config: DecisionRulesConfig) -> GateResult:
    cfg = config.gates.solvency
    checks: list[GateCheck] = []

    bucket = _SOLVENCY_SECTOR_BUCKETS.get(ctx.sector or "", "default")
    threshold = cfg.max_net_debt_to_ebitda_by_sector.get(bucket)

    if threshold is None:
        checks.append(
            GateCheck(
                "net_debt_to_ebitda",
                GateStatus.NOT_EVALUATED,
                f"not a meaningful check for sector bucket {bucket!r}"
                if bucket != "default"
                else "no threshold configured",
            )
        )
    elif ctx.net_debt is None or ctx.ebitda is None or ctx.ebitda == 0:
        checks.append(
            GateCheck(
                "net_debt_to_ebitda", GateStatus.NOT_EVALUATED, "net debt or EBITDA unavailable"
            )
        )
    else:
        ratio = ctx.net_debt / ctx.ebitda
        ok = ratio <= threshold
        checks.append(
            GateCheck(
                "net_debt_to_ebitda",
                GateStatus.PASS if ok else GateStatus.FAIL,
                f"{ratio:.2f}x, max {threshold:.2f}x (sector bucket: {bucket})",
            )
        )

    for name in ("interest_cover", "refi_wall", "altman_z"):
        checks.append(_unimplemented(name))

    veto = any(c.status == GateStatus.FAIL for c in checks)
    return GateResult(
        gate="G2_solvency",
        checks=checks,
        veto=veto,
        reason="a solvency check failed" if veto else "all evaluated checks passed",
    )


def check_g3_investability(ctx: GateContext, config: DecisionRulesConfig) -> GateResult:
    cfg = config.gates.investability
    checks: list[GateCheck] = []

    if ctx.average_daily_dollar_volume is None:
        checks.append(
            GateCheck(
                "adv_liquidity", GateStatus.NOT_EVALUATED, "average dollar volume unavailable"
            )
        )
    else:
        exit_capacity_usd = ctx.average_daily_dollar_volume * cfg.min_adv_multiple_of_position
        ok = exit_capacity_usd >= ctx.reference_position_usd
        checks.append(
            GateCheck(
                "adv_liquidity",
                GateStatus.PASS if ok else GateStatus.FAIL,
                f"{cfg.min_adv_multiple_of_position}d ADV capacity "
                f"${exit_capacity_usd:,.0f}, position ${ctx.reference_position_usd:,.0f}",
            )
        )

    if ctx.market_accessible is None:
        checks.append(
            GateCheck(
                "market_accessible", GateStatus.NOT_EVALUATED, "no market-access flag configured"
            )
        )
    else:
        checks.append(
            GateCheck(
                "market_accessible",
                GateStatus.PASS if ctx.market_accessible else GateStatus.FAIL,
                "accessible" if ctx.market_accessible else "capital controls or ownership cap",
            )
        )

    checks.append(_unimplemented("delisting_risk"))

    veto = any(c.status == GateStatus.FAIL for c in checks)
    return GateResult(
        gate="G3_investability",
        checks=checks,
        veto=veto,
        reason="an investability check failed" if veto else "all evaluated checks passed",
    )


def check_g4_governance(ctx: GateContext, config: DecisionRulesConfig) -> GateResult:
    # No news/filings-text data source is wired up — every governance-veto
    # trigger (fraud allegation, auditor resignation, restatement, etc.)
    # needs one. Reported honestly as unevaluated, never assumed clean.
    checks = [_unimplemented("governance_veto_triggers")]
    return GateResult(
        gate="G4_governance",
        checks=checks,
        veto=False,
        reason="not evaluated — see check detail",
    )


@dataclass(frozen=True)
class GateTrace:
    results: list[GateResult] = field(default_factory=list)

    @property
    def veto(self) -> bool:
        return any(r.veto for r in self.results)

    @property
    def veto_reasons(self) -> list[str]:
        return [f"{r.gate}: {r.reason}" for r in self.results if r.veto]

    @property
    def data_completeness(self) -> float:
        all_checks = [c for r in self.results for c in r.checks]
        evaluable_checks = [c for c in all_checks if c.evaluable]
        if not evaluable_checks:
            return 0.0
        evaluated = sum(1 for c in evaluable_checks if c.status != GateStatus.NOT_EVALUATED)
        return evaluated / len(evaluable_checks)


def run_all_gates(ctx: GateContext, config: DecisionRulesConfig) -> GateTrace:
    return GateTrace(
        results=[
            check_g0_data_integrity(ctx, config),
            check_g1_accounting_quality(ctx, config),
            check_g2_solvency(ctx, config),
            check_g3_investability(ctx, config),
            check_g4_governance(ctx, config),
        ]
    )
