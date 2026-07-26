"""The pipeline's decision path: gates -> bands -> composite -> loan structuring.

This module exists to keep three questions structurally separate, because
conflating them is exactly what legacy bureau-only underwriting does (PRD s2,
s8):

1. **Eligibility gates** are pass/fail hard stops and sit *outside* the score.
   If one trips, :func:`score_farmer` never computes a band, a score or an
   offer - so a lender is never handed a number they could trade off against a
   fraud or insolvency finding.
2. **The composite score** is a weighted sum of the four *creditworthiness*
   parameters in :data:`config.SCORE_WEIGHTS`, and nothing else.
3. **Loan structuring** is derived from production capacity and climate
   exposure alone. Farm size and rainfall *size the loan*; they never reject or
   judge a farmer.

The one-way flow is enforced by construction rather than by convention:
:func:`composite_from_bands` iterates ``config.SCORE_WEIGHTS`` keys, so a
structuring band cannot reach the composite even if a caller passes one in, and
:func:`_structure_loan` receives only the two structuring bands, so it cannot
see a creditworthiness signal.

Absence of data is never penalised. A source with no record for this farmer and
a source that could not be reached both resolve to :data:`config.NEUTRAL_BAND`
(points = 3, the midpoint), and are distinguished only in
``raw_sources_used`` - "this farmer has no co-op history" and "we could not
reach the co-op" are different facts for a lender even though they score
identically.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import config
from connectors import ConnectorUnavailableError, get_connector

from . import bands

__all__ = [
    "check_eligibility_gates",
    "composite_from_bands",
    "fetch_all_records",
    "score_farmer",
    "score_tier",
]

#: A negative listing with no ``months_ago`` cannot be shown to be *recent*, so
#: it is dated far enough back that it can never trip the insolvency gate.
#: Mirrors the same sentinel in :mod:`scoring.bands`.
_UNDATED_LISTING_MONTHS: float = 999.0

#: One concrete action per scored parameter, phrased for the farmer and kept
#: strictly inside their control - PRD s5.3 asks for a lever, not a verdict, so
#: "move to a lower-risk county" is never an acceptable lever.
_LEVERS: dict[str, str] = {
    "cash_flow_stability": (
        "Receive and spend your farm money through one M-Pesa number every "
        "month, including your input purchases before planting, so a steady "
        "12-month record builds up."
    ),
    "repayment_history": (
        "Take one small input loan through your co-op or agrovet and clear "
        "every instalment on time. Even a fully repaid 5,000 shilling loan "
        "creates a repayment record where today there is none."
    ),
    "social_collateral": (
        "Keep your co-op membership active and deliver in every season rather "
        "than skipping one. Unbroken season-by-season delivery is what lifts "
        "this band."
    ),
    "market_linkage": (
        "Sell a larger and more regular share of your harvest through your "
        "co-op or a contracted buyer instead of at the farm gate."
    ),
}

_GENERIC_LEVER: str = (
    "Keep building a consistent record on this parameter over the coming "
    "season."
)


# ---------------------------------------------------------------------------
# Connector fan-out
# ---------------------------------------------------------------------------

def fetch_all_records(farmer_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
    """Fetch every source for ``farmer_id``, returning ``(records, raw_sources_used)``.

    Never propagates a connector exception. An unreachable source becomes an
    empty record with ``raw_sources_used[source] = False``; the engine then
    scores it neutrally. A connector outage must never end a farmer's session
    or surface as a 5xx (PRD s4).
    """
    records: dict[str, dict[str, Any]] = {}
    raw_sources_used: dict[str, bool] = {}

    for source in config.CONNECTOR_SOURCES:
        try:
            # get_connector() raises the same error when a source is configured
            # live with no live implementation registered yet, which is exactly
            # an outage from the engine's point of view.
            record = get_connector(source).fetch(farmer_id)
        except ConnectorUnavailableError:
            records[source] = {}
            raw_sources_used[source] = False
            continue

        # A healthy source with no record for this farmer returns {}. It stays
        # True here: the feed answered, it simply has nothing on this farmer.
        records[source] = dict(record) if record else {}
        raw_sources_used[source] = True

    return records, raw_sources_used


# ---------------------------------------------------------------------------
# Eligibility gates (pass/fail -- never scored)
# ---------------------------------------------------------------------------

def check_eligibility_gates(
    farmer: Mapping[str, Any],
    records: Mapping[str, Any],
) -> dict[str, Any]:
    """Run all four hard-stop gates and return ``{"eligible", "gate_failures"}``.

    Every gate is evaluated, not just the first to fail, so an operator sees
    the whole picture in one pass. Each failure carries a farmer-facing
    ``reason`` and an operator-facing ``detail``; the four codes in
    :data:`config.ELIGIBILITY` are the only codes this function can emit.

    No gate ever trips on *absent* data. A missing KIAMIS record, a missing
    IPRS lookup or an unmeasured plot is a gap in our evidence, not a finding
    against the farmer.
    """
    kiamis = _record(records, "kiamis")
    crb_paygo = _record(records, "crb_paygo")

    candidates = (
        _gate_failed_kyc(farmer, kiamis),
        _gate_unfinanced_value_chain(farmer),
        _gate_out_of_area(farmer, kiamis),
        _gate_active_insolvency(crb_paygo),
    )
    failures = [failure for failure in candidates if failure is not None]

    return {"eligible": not failures, "gate_failures": failures}


def _gate_failed_kyc(
    farmer: Mapping[str, Any],
    kiamis: Mapping[str, Any],
) -> dict[str, str] | None:
    """Three-way national-ID check: typed vs document-extracted vs registry."""
    if not config.ELIGIBILITY.get("require_identity_match"):
        return None

    verification = _as_mapping(kiamis.get("identity_verification"))
    candidates = (
        ("typed by the applicant", _norm_id(farmer.get("national_id"))),
        ("extracted from the ID document", _norm_id(verification.get("extracted_national_id"))),
        ("held in the KIAMIS registry", _norm_id(kiamis.get("national_id"))),
    )
    present = [(label, value) for label, value in candidates if value]

    problems: list[str] = []
    # Only a disagreement between values that are actually present fails. Two
    # matching values and one missing source is a pass.
    if len({value for _, value in present}) > 1:
        problems.append(
            "national ID disagrees across sources: "
            + ", ".join(f"{value} ({label})" for label, value in present)
        )
    # An explicit False is a finding; a missing iprs_match means IPRS was never
    # reached, which is absent data.
    if verification.get("iprs_match") is False:
        problems.append("IPRS returned no match for the submitted identity")

    if not problems:
        return None

    return {
        "code": "failed_kyc",
        "reason": (
            "We could not confirm that the ID number you gave us matches the "
            "official records for your name. Please visit a field agent with "
            "your original national ID so we can check it again - this is a "
            "verification step, and it can be corrected."
        ),
        "detail": "; ".join(problems),
    }


def _gate_unfinanced_value_chain(farmer: Mapping[str, Any]) -> dict[str, str] | None:
    """Crop must be one the lender actually finances."""
    crop = _norm_token(farmer.get("crop"))
    financed = [_norm_token(c) for c in config.ELIGIBILITY["financed_value_chains"]]
    if crop and crop in financed:
        return None

    named_crop = str(farmer.get("crop") or "").strip() or "the crop you entered"
    return {
        "code": "unfinanced_value_chain",
        "reason": (
            f"This programme only finances certain crops at the moment, and "
            f"{named_crop} is not one of them yet. That is a limit on what we "
            f"cover, not a judgement on you or your farm."
        ),
        "detail": (
            f"normalised crop {crop!r} not in "
            f"ELIGIBILITY['financed_value_chains'] ({', '.join(financed)})"
        ),
    }


def _gate_out_of_area(
    farmer: Mapping[str, Any],
    kiamis: Mapping[str, Any],
) -> dict[str, str] | None:
    """Plot must be inside the serviceable footprint *and* corroborated by satellite.

    Both conditions share one gate code with different details: either way the
    consequence is that we cannot underwrite this plot.
    """
    # The farmer-declared county is canonical here because Pre-Qualify has to
    # answer this gate before any connector is called (PRD s5.1); KIAMIS only
    # fills in when the farmer's own record is blank.
    county = _norm_token(farmer.get("county")) or _norm_token(kiamis.get("county"))
    serviceable = [_norm_token(c) for c in config.ELIGIBILITY["serviceable_counties"]]

    reasons: list[str] = []
    details: list[str] = []

    if not county or county not in serviceable:
        named_county = str(farmer.get("county") or "").strip() or "your area"
        reasons.append(
            f"We do not operate in {named_county} yet, so we cannot lend "
            f"against this farm. That is a limit on where our field teams and "
            f"buyers reach, not a judgement on you or your farm."
        )
        details.append(
            f"normalised county {county!r} not in "
            f"ELIGIBILITY['serviceable_counties'] ({', '.join(serviceable)})"
        )

    verified = _positive_number(kiamis.get("land_size_acres_satellite_verified"))
    claimed = _positive_number(kiamis.get("land_size_acres_self_reported"))
    min_ratio = float(config.ELIGIBILITY["min_satellite_to_self_report_ratio"])
    # A zero or absent satellite figure means the plot was never measured, not
    # that it is not there - unmeasured data must not trip a gate.
    if verified is not None and claimed is not None:
        ratio = verified / claimed
        if ratio < min_ratio:
            reasons.append(
                "The satellite check of the location you pinned found much "
                "less farmland than you reported, so we cannot yet confirm "
                "the pin is on your plot. A field agent can re-capture your "
                "GPS pin while standing on the farm."
            )
            details.append(
                f"satellite-to-self-report ratio {ratio:.2f} "
                f"({verified:g} of {claimed:g} acres) is below "
                f"ELIGIBILITY['min_satellite_to_self_report_ratio'] {min_ratio:g}"
            )

    if not reasons:
        return None

    return {
        "code": "out_of_area",
        "reason": " ".join(reasons),
        "detail": "; ".join(details),
    }


def _gate_active_insolvency(crb_paygo: Mapping[str, Any]) -> dict[str, str] | None:
    """Trips on a listing that is both large *and* recent, or on heavy leverage.

    An old, small, cured listing is deliberately left to the repayment-history
    band as a scoring signal rather than promoted to a hard stop.
    """
    gate_kes = float(config.ELIGIBILITY["crb_negative_listing_gate_kes"])
    lookback = float(config.ELIGIBILITY["crb_negative_listing_gate_lookback_months"])
    balance_gate_kes = float(config.ELIGIBILITY["crb_existing_balance_gate_kes"])

    details: list[str] = []

    for listing in crb_paygo.get("negative_listings") or []:
        if not isinstance(listing, Mapping):
            continue
        amount = _number(listing.get("amount_kes"), 0.0)
        months_ago = _number(listing.get("months_ago"), _UNDATED_LISTING_MONTHS)
        if amount >= gate_kes and months_ago <= lookback:
            details.append(
                f"negative listing of KES {amount:,.0f} filed {months_ago:g} "
                f"months ago clears both gate thresholds "
                f"(>= KES {gate_kes:,.0f} and within {lookback:g} months)"
            )

    balance = _number(crb_paygo.get("existing_loan_balance_kes"), 0.0)
    if balance >= balance_gate_kes:
        details.append(
            f"existing loan balance KES {balance:,.0f} is at or above "
            f"ELIGIBILITY['crb_existing_balance_gate_kes'] "
            f"KES {balance_gate_kes:,.0f}"
        )

    if not details:
        return None

    return {
        "code": "active_insolvency",
        "reason": (
            "The credit-bureau check shows a debt that is still unsettled and "
            "large enough that adding another loan on top would not be safe "
            "for you. Once it is cleared or brought down, you can apply again."
        ),
        "detail": "; ".join(details),
    }


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def composite_from_bands(parameter_bands: Mapping[str, Mapping[str, Any]]) -> float:
    """Weighted 0-100 score from the four creditworthiness bands.

    Iterating :data:`config.SCORE_WEIGHTS` - never ``parameter_bands`` - is what
    makes it structurally impossible for ``production_capacity`` or
    ``climate_exposure`` to reach the composite: extra keys are ignored by
    construction, not by a check that could be forgotten.

    The arithmetic runs in :class:`~decimal.Decimal` end to end. Binary floats
    turn an exact 3.35 weighted mean into 3.3499999999999996, which rounds to
    58.7 instead of the hand-verified 58.8, and :func:`round` would additionally
    apply banker's rounding (``round(91.25, 1) == 91.2``). Neither is acceptable
    for a number a lender has to be able to reproduce with a calculator.
    """
    missing = sorted(p for p in config.SCORE_WEIGHTS if p not in parameter_bands)
    if missing:
        raise ValueError(f"no band supplied for scored parameter(s): {missing}")

    weighted_points = sum(
        (
            Decimal(str(config.SCORE_WEIGHTS[p])) * Decimal(int(parameter_bands[p]["points"]))
            for p in config.SCORE_WEIGHTS
        ),
        Decimal(0),
    )
    # Map a mean of 1..5 points onto 0..100 so the neutral band (3) lands on
    # exactly 50 - a thin-file farmer sits in the middle, not at the bottom.
    raw = (weighted_points - 1) / 4 * 100
    return float(raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def score_tier(composite: float) -> str:
    """Farmer-facing tier for ``composite``, per :data:`config.SCORE_TIERS`.

    The farmer is shown a tier rather than a bald number (PRD s5.3).
    """
    tiers = sorted(config.SCORE_TIERS, key=lambda tier: tier[0], reverse=True)
    for minimum, label in tiers:
        if composite >= minimum:
            return label
    return tiers[-1][1]


# ---------------------------------------------------------------------------
# Loan structuring (production capacity + climate exposure only)
# ---------------------------------------------------------------------------

def _structure_loan(
    production_capacity: Mapping[str, Any] | int,
    climate_exposure: Mapping[str, Any] | int,
) -> dict[str, Any]:
    """Size and structure the offer from the two structuring bands only.

    Takes the bands themselves rather than the whole scorecard so that no
    creditworthiness signal is even in scope here.
    """
    cap_points = _band_points(production_capacity)
    clim_points = _band_points(climate_exposure)
    rules = config.LOAN_STRUCTURING

    base = Decimal(str(rules["capacity_base_kes"][cap_points]))
    multiplier = Decimal(str(rules["climate_multiplier"][clim_points]))
    amount = base * multiplier
    amount = min(
        max(amount, Decimal(str(rules["min_loan_floor_kes"]))),
        Decimal(str(rules["max_loan_ceiling_kes"])),
    )
    # Round DOWN to the nearest 500: never quote more than the rules allow.
    rounding = Decimal(str(rules["rounding_kes"]))
    max_loan_amount_kes = int(amount // rounding * rounding)

    term_months = int(rules["term_months_by_climate"][clim_points])
    insurance_threshold = int(rules["insurance_mandatory_at_or_below_climate_points"])
    insurance_mandatory = clim_points <= insurance_threshold

    insurance_note = (
        "index insurance is a condition of this loan"
        if insurance_mandatory
        else "index insurance is not required"
    )
    rationale = (
        f"Production capacity band {cap_points}/5 sets the base at "
        f"KES {int(base):,}; climate exposure band {clim_points}/5 applies a "
        f"x{multiplier:.2f} adjustment and a {term_months}-month term, and "
        f"{insurance_note}. These two parameters size and structure the loan "
        f"only - neither is part of the 0-100 creditworthiness score, and "
        f"neither can make a farmer ineligible."
    )

    return {
        "max_loan_amount_kes": max_loan_amount_kes,
        "term_months": term_months,
        "insurance_mandatory": insurance_mandatory,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Top-level scoring
# ---------------------------------------------------------------------------

def score_farmer(
    farmer: Mapping[str, Any],
    records: Mapping[str, Any] | None = None,
    raw_sources_used: Mapping[str, bool] | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Score one farmer end to end: gates first, then bands, score and offer.

    ``records`` may be supplied by a caller that has already cached the
    connector payloads (PRD s3.2); otherwise every source is fetched here.
    ``as_of`` fixes the date co-op tenure is measured against, which is what
    makes a stored score reproducible after the fact.

    On a gate failure the return value carries the failures, the source audit
    trail and a timestamp - and no bands, no score and no offer at all. That
    absence is deliberate: a hard stop is not a low score, and a lender must
    not be able to weigh one against the other.
    """
    farmer_id = str(farmer.get("farmer_id") or "")

    if records is None:
        records, fetched = fetch_all_records(farmer_id)
        raw_sources_used = dict(raw_sources_used) if raw_sources_used is not None else fetched
    elif raw_sources_used is None:
        # Handed records but not the audit flags: a source with a key in
        # `records` is one the caller heard back from, empty record or not.
        raw_sources_used = {s: s in records for s in config.CONNECTOR_SOURCES}
    else:
        raw_sources_used = dict(raw_sources_used)

    gates = check_eligibility_gates(farmer, records)
    scored_at = _utc_now_iso()

    if not gates["eligible"]:
        return {
            "farmer_id": farmer_id,
            "eligible": False,
            "gate_failures": gates["gate_failures"],
            "raw_sources_used": raw_sources_used,
            "scored_at": scored_at,
        }

    kiamis = _record(records, "kiamis")
    cooperative = _record(records, "cooperative")
    agrovet_mpesa = _record(records, "agrovet_mpesa")
    crb_paygo = _record(records, "crb_paygo")
    climate = _record(records, "climate")

    parameter_bands = {
        "cash_flow_stability": bands.band_cash_flow_stability(agrovet_mpesa),
        "repayment_history": bands.band_repayment_history(crb_paygo, cooperative),
        "social_collateral": bands.band_social_collateral(cooperative, as_of=as_of),
        "market_linkage": bands.band_market_linkage(cooperative),
    }

    # Structuring bands are computed alongside, and handed only to
    # _structure_loan(). They are reported for transparency, never weighted.
    production_capacity = bands.band_production_capacity(kiamis)
    climate_exposure = bands.band_climate_exposure(climate, crb_paygo)

    composite = composite_from_bands(parameter_bands)

    return {
        "farmer_id": farmer_id,
        "eligible": True,
        "gate_failures": [],
        "production_capacity": production_capacity,
        "climate_exposure": climate_exposure,
        "parameter_bands": parameter_bands,
        "composite_score_0_100": composite,
        "score_tier": score_tier(composite),
        "loan_structuring": _structure_loan(production_capacity, climate_exposure),
        "raw_sources_used": raw_sources_used,
        "improvement_levers": _improvement_levers(parameter_bands),
        "scored_at": scored_at,
    }


def _improvement_levers(
    parameter_bands: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The 1-2 weakest scored parameters, each with one action the farmer owns.

    Ties break towards the more heavily weighted parameter, so the lever listed
    first is the one worth the most points.
    """
    improvable = [
        p
        for p in config.SCORE_WEIGHTS
        if p in parameter_bands
        and int(parameter_bands[p]["points"]) < config.BAND_MAX_POINTS
    ]
    ranked = sorted(
        improvable,
        key=lambda p: (int(parameter_bands[p]["points"]), -config.SCORE_WEIGHTS[p], p),
    )

    return [
        {
            "parameter": p,
            "lever": _LEVERS.get(p, _GENERIC_LEVER),
            "points_available": config.BAND_MAX_POINTS - int(parameter_bands[p]["points"]),
        }
        for p in ranked[:2]
    ]


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    """Timezone-aware UTC ISO 8601, ``Z``-suffixed for the stored score record."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _record(records: Mapping[str, Any] | None, source: str) -> dict[str, Any]:
    """One source's payload as a plain dict; ``{}`` for missing or malformed.

    Copied so a band function cannot mutate a caller's connector cache.
    """
    value = records.get(source) if records else None
    return dict(value) if isinstance(value, Mapping) else {}


def _norm_id(value: Any) -> str:
    """Compare IDs on their alphanumerics only - typed input carries spaces."""
    return "".join(ch for ch in str(value or "") if ch.isalnum()).upper()


def _norm_token(value: Any) -> str:
    """Normalise a crop or county to the ``lower_snake`` form config uses."""
    text = str(value or "").strip().lower()
    flattened = "".join(ch if ch.isalnum() else "_" for ch in text)
    return "_".join(part for part in flattened.split("_") if part)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _positive_number(value: Any) -> float | None:
    """``None`` unless ``value`` is a usable positive measurement."""
    number = _number(value, 0.0)
    return number if number > 0 else None


def _band_points(band: Mapping[str, Any] | int) -> int:
    """Points from a band dict (or a bare int), clamped into the 1-5 range.

    Clamping keeps a malformed band from indexing off the end of the
    LOAN_STRUCTURING tables.
    """
    raw = band["points"] if isinstance(band, Mapping) else band
    return max(config.BAND_MIN_POINTS, min(config.BAND_MAX_POINTS, int(raw)))
