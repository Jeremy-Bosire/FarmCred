"""Per-parameter banding rules -- the atomic unit of the scorecard.

Every scored quantity in this pipeline resolves to a *band*, never a raw
number::

    {"points": 1..5, "label": "Very stable", "why": "Mobile-money inflows ..."}

Three properties of that shape are load-bearing:

**Points always run 1 (worst) to 5 (best)**, including
:func:`band_climate_exposure`, where 5 means *least* exposed. A caller can
therefore weight, compare or average two bands without knowing which parameter
produced them, and no sign flip is hidden anywhere in the engine.

**``why`` is a deliverable, not a debug string.** It is what the farmer is shown
(PRD s5.3) and what a lender audits the decision against, so it quotes the
actual evidence -- real months, shillings, acres, percentages -- instead of
restating whichever threshold happened to be crossed.

**Running out of evidence resolves to the midpoint.** Every such path returns
:func:`neutral_band` (points = 3), never the floor. A farmer with no bureau file
is invisible to traditional underwriting; scoring that invisibility as
delinquency is the precise failure this pipeline exists to correct (PRD s9). The
same rule covers a malformed partner payload: a live feed that hands back a
string where a number was promised must produce a neutral band, never a
traceback in a farmer's session.

Two of the six functions here -- :func:`band_production_capacity` and
:func:`band_climate_exposure` -- feed *loan structuring only*. They never reach
``config.SCORE_WEIGHTS`` and can never fail or downgrade a farmer, which is why
their ``why`` strings say so out loud.

This module imports only :mod:`config` and the standard library. It must not
import :mod:`scoring.engine`: the engine composes bands, so the dependency would
be circular, and keeping each function pure in one partner payload is what lets
a golden test hand-verify a band without standing up a connector.

Thresholds here are illustrative pilot placeholders, replaced by a fitted model
once real repayment outcomes accumulate (PRD s8).
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import config

__all__ = [
    "band_cash_flow_stability",
    "band_climate_exposure",
    "band_market_linkage",
    "band_production_capacity",
    "band_repayment_history",
    "band_social_collateral",
    "neutral_band",
]


# ---------------------------------------------------------------------------
# Tier tables
# ---------------------------------------------------------------------------
# ``{points: (label, summary clause)}``. The clause is the tail of the ``why``
# sentence; carrying it alongside the label avoids assembling farmer-facing
# prose by gluing an article onto a noun at runtime.

_CASH_FLOW_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Very stable", "cash flow is very stable"),
    4: ("Stable", "cash flow is stable"),
    3: ("Moderately stable", "cash flow is moderately stable"),
    2: ("Thin or uneven", "cash flow is thin or uneven"),
    1: ("Very uneven", "cash flow is very uneven"),
}

_REPAYMENT_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Excellent", "this is an excellent repayment record"),
    4: ("Good", "this is a good repayment record"),
    3: ("Limited history", "there is limited repayment history, so it is scored mid-range"),
    2: ("Some arrears", "past arrears hold this band down"),
    1: ("Serious arrears", "large recent arrears hold this band at the floor"),
}

_SOCIAL_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Deeply rooted member", "this is a deeply rooted cooperative member"),
    4: ("Long-standing member", "this is a long-standing cooperative member"),
    3: ("Established member", "this is an established cooperative member"),
    2: ("Newer member", "this is still a newer cooperative member"),
    1: ("New member", "this is a brand-new cooperative member"),
}

_MARKET_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Very strong buyer link", "the link to a formal buyer is very strong"),
    4: ("Strong buyer link", "the link to a formal buyer is strong"),
    3: ("Moderate buyer link", "the link to a formal buyer is moderate"),
    2: ("Weak buyer link", "the link to a formal buyer is weak"),
    1: ("Occasional buyer link", "produce reaches a formal buyer only occasionally"),
}

_CAPACITY_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Very large holding", "the farm can carry the largest input package offered"),
    4: ("Large holding", "the farm can carry a large input package"),
    3: ("Moderate holding", "the farm can carry a moderate input package"),
    2: ("Small holding", "the farm can carry a small input package"),
    1: ("Very small holding", "the farm can carry only a very small input package"),
}

# 5 = LEAST exposed, so this table reads downwards from "low".
_CLIMATE_TIERS: Dict[int, Tuple[str, str]] = {
    5: ("Low exposure", "the coming season carries low weather risk"),
    4: ("Moderate exposure", "the coming season carries moderate weather risk"),
    3: ("Elevated exposure", "the coming season carries elevated weather risk"),
    2: ("High exposure", "the coming season carries high weather risk"),
    1: ("Severe exposure", "the coming season carries severe weather risk"),
}

#: Appended to both structuring bands. The three-way split (gate / score /
#: structure) is invisible to a farmer unless we say it, and a farmer whose
#: ward is drought-prone deserves to be told it did not count against them.
_STRUCTURING_NOTE = "this sizes the loan only and never affects the credit score"

#: A band with any adverse bureau listing on it never tops out, however good the
#: proxies are.
_BLEMISHED_FILE_CAP = config.BAND_MAX_POINTS - 1


# ---------------------------------------------------------------------------
# Payload hardening
# ---------------------------------------------------------------------------
# Mock payloads are well-formed by construction; live partner feeds are not.
# Every helper below turns junk into "absent", which the band functions already
# know how to resolve to the neutral midpoint.


def _as_dict(payload: Any) -> Dict[str, Any]:
    """Return ``payload`` if it is a mapping-shaped record, else ``{}``."""
    return payload if isinstance(payload, dict) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` as a list, treating anything else as an empty series."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _num(value: Any) -> Optional[float]:
    """Coerce a partner field to a finite float, or ``None`` if it is not one.

    Numeric strings are accepted because JSON feeds routinely quote amounts.
    ``bool`` is rejected despite being an ``int`` subclass -- a flag arriving
    where a count belongs is malformed, not the number 1. NaN and infinity are
    rejected too: they would propagate silently through ``pstdev`` and make
    every threshold comparison false, which reads as the *worst* band rather
    than as missing data.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _num_or(value: Any, default: float) -> float:
    """:func:`_num` with a fallback, for fields that have a defined absence."""
    number = _num(value)
    return default if number is None else number


def _count(value: Any, default: int = 0) -> int:
    """:func:`_num_or` for whole-number fields (season counts, transactions)."""
    return int(_num_or(value, float(default)))


def _band(points: int, tiers: Dict[int, Tuple[str, str]], clauses: List[str]) -> Dict[str, Any]:
    """Assemble the band dict: clamped points, tier label, one-sentence ``why``."""
    safe_points = _clamp(points)
    label, summary = tiers[safe_points]
    body = ", ".join(clause for clause in clauses if clause)
    return {"points": safe_points, "label": label, "why": f"{body}; {summary}."}


def _clamp(points: int) -> int:
    """Hold points inside 1..5 so no adjustment can invent a sixth band."""
    return max(config.BAND_MIN_POINTS, min(config.BAND_MAX_POINTS, points))


def _kes(amount: float) -> str:
    """Shillings, grouped, no cents -- the way an offer letter states them."""
    return f"KES {amount:,.0f}"


def _acres(value: float) -> str:
    """Acreage without trailing zeros: 2.6, 0.75, 3 rather than 2.60, 3.00."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def neutral_band(reason: str = "") -> Dict[str, Any]:
    """A copy of :data:`config.NEUTRAL_BAND`, optionally naming what was missing.

    A *copy*, always: callers mutate bands (the engine tags them, the CLI
    truncates them), and handing out the shared config dict would let one
    farmer's scorecard rewrite the next farmer's.
    """
    band = dict(config.NEUTRAL_BAND)
    if reason:
        band["why"] = f"{config.NEUTRAL_BAND['why']} In this case: {reason}."
    return band


def _delivery_adjustment(cooperative: Dict[str, Any]) -> Tuple[int, Optional[float], int, int]:
    """Delivery consistency, shared by social collateral and market linkage.

    Returns ``(adjustment, ratio, delivered, total)`` with ``ratio`` ``None``
    when the co-op reported no season count -- absent, so no adjustment.
    """
    delivered = _count(cooperative.get("seasons_with_delivery"))
    total = _count(cooperative.get("seasons_total"))
    if total <= 0:
        return 0, None, delivered, total
    ratio = delivered / total
    adjustment = 1 if ratio >= 0.90 else -1 if ratio < 0.50 else 0
    return adjustment, ratio, delivered, total


def _delivery_clause(adjustment: int, ratio: Optional[float], delivered: int, total: int) -> str:
    """Farmer-readable evidence for :func:`_delivery_adjustment`."""
    if ratio is None:
        return "with no season-by-season delivery count on file"
    clause = f"having delivered produce in {delivered} of {total} seasons ({ratio * 100:.0f}%)"
    if adjustment > 0:
        return clause + ", consistency that lifted the band a step"
    if adjustment < 0:
        return clause + ", a gap that pulled the band down a step"
    return clause


# ---------------------------------------------------------------------------
# The four creditworthiness parameters (weighted into the composite score)
# ---------------------------------------------------------------------------


def band_cash_flow_stability(agrovet_mpesa: dict) -> dict:
    """Band the steadiness of mobile-money inflows.

    Steadiness, not size: the coefficient of variation is what a lender can
    lend against, because a farmer whose income arrives in predictable amounts
    can meet a fixed instalment even on a modest turnover. Seasonality is
    tolerated deliberately -- tea bonus months and horticulture export cycles
    are lumpy by nature, not disorderly.
    """
    record = _as_dict(agrovet_mpesa)
    if not record:
        return neutral_band("no mobile-money or agrovet record was returned for this farmer")

    inflows = [
        amount
        for amount in (_num(entry) for entry in _as_list(record.get("monthly_inflows_kes")))
        if amount is not None
    ]
    if not inflows:
        return neutral_band("the mobile-money record carried no usable monthly inflow figures")

    months = len(inflows)
    mean = sum(inflows) / months
    if mean <= 0:
        # Recorded inflows that average to nothing are evidence, not absence:
        # there is no income stream here to service an instalment from.
        return _band(
            config.BAND_MIN_POINTS,
            _CASH_FLOW_TIERS,
            [
                f"Across {months} months of mobile-money history the average inflow was "
                f"{_kes(mean)}, so there is no income stream to repay an instalment from"
            ],
        )

    cv = statistics.pstdev(inflows) / mean
    median = statistics.median(inflows)
    variability_base = (
        5 if cv <= 0.25
        else 4 if cv <= 0.40
        else 3 if cv <= 0.60
        else 2 if cv <= 0.85
        else 1
    )

    base = variability_base
    too_thin = median < 3000
    if too_thin:
        base = min(base, 2)  # too small a monthly float to service a loan from

    adjustment = 0
    clauses = [
        f"Mobile-money inflows averaged {_kes(mean)} a month and varied by "
        f"{'only ' if variability_base >= 4 else ''}{cv * 100:.0f}% month to month "
        f"across {months} months"
    ]

    if too_thin:
        thin_clause = (
            f"but a typical month of {_kes(median)} is too small to carry a loan instalment"
        )
        if variability_base > 2:
            thin_clause += ", which caps the band"
        clauses.append(thin_clause)

    if record.get("input_purchases_before_planting"):
        adjustment += 1
        clauses.append(
            "inputs were paid for before planting, which shows the season is planned ahead"
        )

    unmapped = _count(record.get("unmapped_till_transactions"))
    if unmapped > 15:
        adjustment -= 1
        clauses.append(
            f"though {unmapped} till payments could not be matched to any known merchant"
        )

    points = _clamp(base + adjustment)

    # Fewer than six months is too short a window to trust either way, so the
    # band is held at the neutral midpoint rather than rewarded or punished.
    low_confidence_cap = int(config.NEUTRAL_BAND["points"])
    if months < 6:
        if points > low_confidence_cap:
            clauses.append(
                f"and with only {months} months of history the band is held at the "
                "neutral midpoint until more of the year is visible"
            )
        points = min(points, low_confidence_cap)

    return _band(points, _CASH_FLOW_TIERS, clauses)


def band_repayment_history(crb_paygo: dict, cooperative: dict) -> dict:
    """Band demonstrated repayment behaviour, formal or informal.

    Two sources, because most smallholders have no bureau file at all. Where a
    file exists the worst listing drives the band; where none exists, co-op
    input-loan deductions and a pay-as-you-go tradeline stand in as proxies, and
    the starting point is the midpoint -- a thin file is an absence of evidence,
    not evidence of default.
    """
    crb = _as_dict(crb_paygo)
    coop = _as_dict(cooperative)
    if not crb and not coop:
        return neutral_band(
            "neither the credit bureau nor a cooperative returned a record for this farmer"
        )

    has_file = bool(crb.get("has_crb_file"))
    listings = [
        entry for entry in _as_list(crb.get("negative_listings")) if isinstance(entry, dict)
    ]
    paygo = crb.get("paygo_tradeline")
    deductions = _count(coop.get("input_loan_deductions_last_12mo"))
    balance = _num_or(crb.get("existing_loan_balance_kes"), 0.0)

    clauses: List[str] = []
    worst: Optional[Tuple[int, float, float]] = None

    if has_file:
        if not listings:
            base = config.BAND_MAX_POINTS
            clauses.append("The credit-bureau file is clean, with no negative listings on it")
        else:
            base = config.BAND_MAX_POINTS
            for entry in listings:
                amount = _num_or(entry.get("amount_kes"), 0.0)
                months_ago = _num_or(entry.get("months_ago"), 999.0)
                if months_ago <= 12:
                    severity = 1 if amount >= 20_000 else 2 if amount >= 5_000 else 3
                elif months_ago <= 24:
                    severity = 2 if amount >= 20_000 else 3
                else:
                    severity = 4
                if worst is None or severity < worst[0]:
                    worst = (severity, amount, months_ago)
                base = min(base, severity)  # the worst listing drives the band
            _, worst_amount, worst_age = worst if worst else (4, 0.0, 999.0)
            plural = "s" if len(listings) != 1 else ""
            clauses.append(
                f"The credit-bureau file carries {len(listings)} negative listing{plural}, the "
                f"most serious being {_kes(worst_amount)} from {worst_age:.0f} months ago"
            )
        if balance > 100_000:
            base -= 1
            clauses.append(
                f"an existing loan balance of {_kes(balance)} already stretches this household"
            )
    else:
        if paygo is None and deductions == 0:
            return neutral_band(
                "there is no credit-bureau file, no pay-as-you-go account and no cooperative "
                "input-loan deductions to judge repayment on"
            )
        base = int(config.NEUTRAL_BAND["points"])
        clauses.append(
            "No credit-bureau file exists for this farmer, so repayment is judged from proxies "
            "starting at the neutral midpoint rather than from a blank record"
        )

    adjustment = 0
    if paygo in ("current_good", "completed"):
        adjustment += 1
        clauses.append(
            "a pay-as-you-go asset loan is being repaid on time"
            if paygo == "current_good"
            else "an earlier pay-as-you-go asset loan was repaid in full"
        )
    elif paygo == "defaulted":
        adjustment -= 2
        clauses.append("a pay-as-you-go asset loan was left in default")

    if deductions >= 2:
        adjustment += 1
        clauses.append(
            f"the cooperative recovered input-loan instalments from {deductions} deliveries in the "
            "last 12 months, repayment behaviour a bureau would never see"
        )
    elif deductions:
        clauses.append(
            f"the cooperative recovered an input-loan instalment from {deductions} delivery in the "
            "last 12 months"
        )

    points = _clamp(base + adjustment)
    if has_file and listings:
        if points > _BLEMISHED_FILE_CAP:
            clauses.append("though a file carrying any listing is held one step below the top band")
        points = min(points, _BLEMISHED_FILE_CAP)

    return _band(points, _REPAYMENT_TIERS, clauses)


def band_social_collateral(cooperative: dict, *, as_of: date | None = None) -> dict:
    """Band cooperative membership tenure as informal collateral.

    Years of standing in a co-op is a reputational stake a farmer will not
    lightly forfeit, which is the closest thing to security most smallholders
    can offer. ``as_of`` is injectable so a golden test or a re-score of a
    historical application is reproducible rather than drifting with the clock.
    """
    coop = _as_dict(cooperative)
    if not coop:
        return neutral_band("this farmer has no cooperative record to draw membership history from")

    if as_of is None:
        as_of = date.today()
    elif isinstance(as_of, datetime):
        # datetime is a date subclass, but subtracting a date from it raises.
        as_of = as_of.date()

    raw_since = coop.get("member_since")
    member_since = _parse_iso_date(raw_since)
    if member_since is None:
        return neutral_band(
            f"the cooperative record's membership start date ({raw_since!r}) could not be read"
        )

    tenure_years = (as_of - member_since).days / 365.25
    base = (
        5 if tenure_years >= 8
        else 4 if tenure_years >= 5
        else 3 if tenure_years >= 3
        else 2 if tenure_years >= 1
        else 1
    )

    adjustment, ratio, delivered, total = _delivery_adjustment(coop)
    points = _clamp(base + adjustment)

    name = str(coop.get("cooperative_name") or "the cooperative")
    clauses = [
        f"A member of {name} for {tenure_years:.1f} years (since {member_since.isoformat()})",
        _delivery_clause(adjustment, ratio, delivered, total),
    ]
    return _band(points, _SOCIAL_TIERS, clauses)


def band_market_linkage(cooperative: dict) -> dict:
    """Band the strength of the route from farm gate to a paying buyer.

    A farmer selling through a co-op or off-taker has a predictable payment
    channel -- and one an input loan can be recovered from at source. An
    unrecognised delivery tier is treated as missing data, not bad data: a
    partner renaming its tiers must not quietly downgrade its members.
    """
    coop = _as_dict(cooperative)
    if not coop:
        return neutral_band("this farmer has no cooperative or off-taker record on file")

    tier = str(coop.get("delivery_tier") or "").strip().lower()
    base = {"top": 5, "high": 4, "mid": 3, "low": 2, "occasional": 1}.get(tier)
    if base is None:
        return neutral_band(
            f"the cooperative reported a delivery tier ({tier or 'blank'}) this scorecard does not "
            "recognise, which is missing information rather than a poor result"
        )

    adjustment, ratio, delivered, total = _delivery_adjustment(coop)
    points = _clamp(base + adjustment)

    name = str(coop.get("cooperative_name") or "the cooperative")
    clauses = [
        f"{name} places this farmer in its '{tier}' delivery tier",
        _delivery_clause(adjustment, ratio, delivered, total),
    ]
    return _band(points, _MARKET_TIERS, clauses)


# ---------------------------------------------------------------------------
# Structuring parameters -- loan sizing only, never the score, never a gate
# ---------------------------------------------------------------------------


def band_production_capacity(kiamis: dict) -> dict:
    """Band how much production the land can plausibly support.

    Satellite-verified acreage wins over self-report when it exists, because it
    is the figure the lender can defend. This band sets loan *size* only: a
    small farm gets a smaller package, never a worse score (PRD s8).
    """
    record = _as_dict(kiamis)
    if not record:
        return neutral_band("no KIAMIS land record was returned for this farmer")

    self_reported = _num(record.get("land_size_acres_self_reported"))
    verified = _num(record.get("land_size_acres_satellite_verified"))
    satellite_used = verified is not None and verified > 0
    acres = verified if satellite_used else self_reported
    if acres is None:
        return neutral_band("the KIAMIS record carried no usable land size for this farmer")

    base = (
        5 if acres >= 5
        else 4 if acres >= 2.5
        else 3 if acres >= 1.5
        else 2 if acres >= 0.75
        else 1
    )

    livestock = _count(record.get("livestock_units"))
    secondary = _as_list(record.get("secondary_crops"))
    diversified = livestock >= 3 or len(secondary) >= 2
    points = _clamp(base + (1 if diversified else 0))

    if satellite_used and self_reported is not None:
        acreage_clause = (
            f"Satellite imagery verifies {_acres(float(acres))} acres under crop "
            f"against {_acres(self_reported)} acres self-reported"
        )
    elif satellite_used:
        acreage_clause = f"Satellite imagery verifies {_acres(float(acres))} acres under crop"
    else:
        acreage_clause = (
            f"Land size is {_acres(float(acres))} acres as self-reported, with no satellite "
            "verification available yet"
        )

    livestock_text = f"{livestock} livestock unit{'' if livestock == 1 else 's'}"
    crops_text = (
        f"{len(secondary)} secondary crop{'' if len(secondary) == 1 else 's'}"
        if secondary
        else "no secondary crops"
    )
    if diversified:
        widened = " plus ".join(
            part
            for part in (
                livestock_text if livestock >= 3 else "",
                crops_text if len(secondary) >= 2 else "",
            )
            if part
        )
        diversity_clause = f"and {widened} widen the income base enough to lift the band a step"
    else:
        diversity_clause = f"with {livestock_text} and {crops_text} alongside the main crop"

    band = _band(points, _CAPACITY_TIERS, [acreage_clause, diversity_clause])
    band["why"] = _with_structuring_note(band["why"])
    return band


def band_climate_exposure(climate: dict, crb_paygo: dict) -> dict:
    """Band weather risk for the coming season. 5 = LEAST exposed.

    Points run the same direction as every other band so the engine never has
    to flip a sign, and this band sizes the loan and sets the tenor and the
    insurance condition -- it can never reject a farmer or dent their score. It
    is also the one band a farmer can actively improve: irrigation ownership
    lifts it a step, which is worth saying out loud.
    """
    record = _as_dict(climate)
    if not record:
        return neutral_band("no climate or satellite record was returned for this ward")

    exposure = str(record.get("regional_exposure") or "").strip().lower()
    base = {"low": 5, "moderate": 4, "elevated": 3, "high": 2, "severe": 1}.get(exposure)
    if base is None:
        # Unlike the score-bearing bands this one has no neutral-return path:
        # the engine always needs a structuring band, so an unrecognised
        # regional rating starts at the midpoint and the satellite readings
        # below still get their say.
        base = int(config.NEUTRAL_BAND["points"])
        clauses = ["The ward has no recognised regional exposure rating, so it starts mid-range"]
    else:
        clauses = [f"The ward is rated {exposure} for regional climate exposure"]

    adjustment = 0

    ndvi = _num(record.get("ndvi_anomaly"))
    if ndvi is not None:
        if ndvi < -0.15:
            adjustment -= 1
            clauses.append(
                f"crop greenness is running {abs(ndvi):.2f} below normal, which costs a step"
            )
        elif ndvi > 0.05:
            adjustment += 1
            clauses.append(
                f"crop greenness is running {abs(ndvi):.2f} above normal, which earns a step"
            )
        else:
            clauses.append(f"crop greenness is close to normal ({ndvi:+.2f})")

    deficit = _num(record.get("rainfall_deficit_pct"))
    if deficit is not None:
        if deficit >= 50:
            adjustment -= 2
            clauses.append(f"rainfall is {deficit:.0f}% below normal, which costs two steps")
        elif deficit >= 30:
            adjustment -= 1
            clauses.append(f"rainfall is {deficit:.0f}% below normal, which costs a step")
        elif deficit < 0:
            # A live feed reports a wet season as a negative deficit.
            clauses.append(f"rainfall is running {abs(deficit):.0f}% above normal")
        else:
            clauses.append(f"rainfall is {deficit:.0f}% below normal")

    if _as_dict(crb_paygo).get("sunculture_owner"):
        adjustment += 1
        # The F003 story: the farmer's own investment buys back a step the
        # weather took off them. Never leave this implicit.
        clauses.append(
            "but this farmer owns a SunCulture solar irrigation pump, so the band is lifted a "
            "step back for being less dependent on the rains"
        )

    points = _clamp(base + adjustment)
    band = _band(points, _CLIMATE_TIERS, clauses)
    band["why"] = _with_structuring_note(band["why"])
    return band


def _with_structuring_note(why: str) -> str:
    """Append the loan-sizing disclaimer to a structuring band's ``why``."""
    return f"{why.rstrip('.')} -- {_STRUCTURING_NOTE}."


def _parse_iso_date(value: Any) -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` date, or ``None`` if it is unreadable.

    Live partner feeds sometimes send a full timestamp where the schema says
    date, so a trailing time component is tolerated rather than treated as
    missing membership history.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    for candidate in (text, text[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None
