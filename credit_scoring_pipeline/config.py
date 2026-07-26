"""Central configuration for the smallholder credit-scoring pipeline.

Everything a pilot operator might need to retune lives here, not scattered
through the scoring code. Three groups matter most:

``CONNECTOR_MODE``
    Per-source ``"mock"`` / ``"live"`` switch. Flipping one entry changes only
    that connector's data source -- gates, bands and the composite calculation
    require no code change (PRD s9).

``ELIGIBILITY``
    The hard pass/fail gates. These sit *outside* the score entirely: a farmer
    either clears them or scoring never runs.

``SCORE_WEIGHTS``
    Weights for the four *creditworthiness* parameters that compose the 0-100
    score. Production Capacity and Climate Exposure are deliberately absent --
    they size the loan, they never judge the farmer (PRD s8).

Band-threshold numbers in this module and in :mod:`scoring.bands` are
illustrative placeholders for the pilot. They get replaced by a fitted model
once real loan-performance outcomes accumulate; the connector layer is built so
that swap touches nothing else.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Final, List

# ---------------------------------------------------------------------------
# Connector modes
# ---------------------------------------------------------------------------

#: Every external data source the pipeline knows how to talk to.
CONNECTOR_SOURCES: Final[tuple[str, ...]] = (
    "kiamis",
    "cooperative",
    "agrovet_mpesa",
    "crb_paygo",
    "climate",
)


def _env_mode(source: str, default: str = "mock") -> str:
    """Read ``CONNECTOR_MODE_<SOURCE>`` from the environment.

    Unset or unrecognised values fall back to ``default`` so a typo in a
    deployment env file can never silently point a connector at a live partner
    feed (or, worse, at mock data in production without saying so).
    """
    raw = os.getenv(f"CONNECTOR_MODE_{source.upper()}", default)
    mode = (raw or "").strip().lower()
    return mode if mode in ("mock", "live") else default


#: Source -> ``"mock" | "live"``. Env vars win, so the pilot flips one feed at a
#: time without editing this file.
CONNECTOR_MODE: Dict[str, str] = {s: _env_mode(s) for s in CONNECTOR_SOURCES}


# ---------------------------------------------------------------------------
# Eligibility gates (pass/fail -- never scored)
# ---------------------------------------------------------------------------

ELIGIBILITY: Dict[str, Any] = {
    # Value chains the lender actually finances. Anything else is a coverage
    # limitation, communicated as such -- not a judgement on the farmer.
    "financed_value_chains": [
        "maize",
        "tea",
        "coffee",
        "dairy",
        "avocado",
        "french_beans",
        "potato",
    ],
    # Counties with a field-agent footprint and an off-taker relationship.
    "serviceable_counties": [
        "nakuru",
        "kericho",
        "bomet",
        "nyeri",
        "muranga",
        "kiambu",
        "meru",
        "kirinyaga",
        "uasin_gishu",
        "trans_nzoia",
    ],
    # Land-size bands accepted at Pre-Qualify (smallholder definition).
    "land_size_bands": ["under_1_acre", "1_to_2_acres", "2_to_5_acres", "over_5_acres"],
    # An "active insolvency" gate trips only when a negative listing is BOTH
    # large enough and recent enough. An old, small, cured listing is a scoring
    # signal, not a hard stop.
    "crb_negative_listing_gate_kes": 50_000,
    "crb_negative_listing_gate_lookback_months": 24,
    # A balance this large means the farmer is already fully levered.
    "crb_existing_balance_gate_kes": 200_000,
    # Three-way national-ID check (typed / document-extracted / registry).
    "require_identity_match": True,
    # Satellite-verified acreage this far below self-report suggests the pin is
    # not on the farmer's plot. Expressed as a ratio of verified to claimed.
    "min_satellite_to_self_report_ratio": 0.35,
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

#: The four creditworthiness parameters and their weights. MUST sum to 1.0.
#: Deliberately excludes production capacity and climate exposure.
SCORE_WEIGHTS: Dict[str, float] = {
    "cash_flow_stability": 0.35,
    "repayment_history": 0.30,
    "market_linkage": 0.20,
    "social_collateral": 0.15,
}

#: Parameters that size/structure the loan. Never part of the composite score.
STRUCTURING_PARAMETERS: Final[tuple[str, ...]] = (
    "production_capacity",
    "climate_exposure",
)

#: Band points are always 1 (worst) .. 5 (best) for *every* parameter,
#: including climate exposure -- 5 there means "least exposed".
BAND_MIN_POINTS: Final[int] = 1
BAND_MAX_POINTS: Final[int] = 5

#: Returned whenever a source has no record for this farmer, or a live feed is
#: unavailable. The midpoint, never the floor: absence of data must never be
#: scored as evidence of bad behaviour (PRD s9).
NEUTRAL_BAND: Dict[str, Any] = {
    "points": 3,
    "label": "Neutral - no data",
    "why": (
        "No data available from this source. Scored at the neutral midpoint so "
        "that missing information is never treated as evidence of bad "
        "behaviour."
    ),
}


# ---------------------------------------------------------------------------
# Loan structuring (derived from production capacity + climate exposure only)
# ---------------------------------------------------------------------------

LOAN_STRUCTURING: Dict[str, Any] = {
    # Plausible seasonal input financing need by production-capacity band.
    "capacity_base_kes": {1: 15_000, 2: 30_000, 3: 60_000, 4: 110_000, 5: 180_000},
    # Climate exposure haircut. 5 = least exposed, so it earns a small uplift.
    "climate_multiplier": {1: 0.60, 2: 0.75, 3: 0.90, 4: 1.00, 5: 1.10},
    # Shorter tenor where the season is more likely to fail.
    "term_months_by_climate": {1: 6, 2: 6, 3: 9, 4: 12, 5: 12},
    # At or below this climate band, index insurance is a condition of the loan.
    "insurance_mandatory_at_or_below_climate_points": 3,
    "max_loan_ceiling_kes": 250_000,
    "min_loan_floor_kes": 5_000,
    "rounding_kes": 500,
}


# ---------------------------------------------------------------------------
# Score presentation (farmer-facing tiers -- PRD s5.3)
# ---------------------------------------------------------------------------

#: The farmer sees a tier, not a bald number. ``(inclusive_min, label)``,
#: highest first.
SCORE_TIERS: List[tuple[int, str]] = [
    (80, "Strong"),
    (65, "Good"),
    (50, "Building"),
    (35, "Early"),
    (0, "Needs support"),
]


# ---------------------------------------------------------------------------
# Persistence / operations
# ---------------------------------------------------------------------------

#: Farmer profile store, consent log and score history.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./farmcred.db")

#: Deletion / anonymisation schedule for applicants who never convert, per the
#: DPA storage-limitation principle.
CONSENT_LOG_RETENTION_DAYS: int = int(os.getenv("CONSENT_LOG_RETENTION_DAYS", "180"))

#: Data-controller registration reference. Required before processing begins.
ODPC_REGISTRATION_ID: str = os.getenv("ODPC_REGISTRATION_ID", "ODPC-PENDING-PILOT")

#: Stub loan-origination system the pipeline hands decisions off to.
LOS_STUB_BASE_URL: str = os.getenv("LOS_STUB_BASE_URL", "http://localhost:8000/v1/los")


# ---------------------------------------------------------------------------
# Live-feed credentials (unused in mock mode; documented in README s7)
# ---------------------------------------------------------------------------

DARAJA_CONSUMER_KEY: str = os.getenv("DARAJA_CONSUMER_KEY", "")
DARAJA_CONSUMER_SECRET: str = os.getenv("DARAJA_CONSUMER_SECRET", "")
DARAJA_CALLBACK_BASE_URL: str = os.getenv("DARAJA_CALLBACK_BASE_URL", "")
CRB_METROPOL_API_KEY: str = os.getenv("CRB_METROPOL_API_KEY", "")
CRB_METROPOL_BASE_URL: str = os.getenv("CRB_METROPOL_BASE_URL", "")
KIAMIS_MOU_ENDPOINT: str = os.getenv("KIAMIS_MOU_ENDPOINT", "")
KIAMIS_MOU_API_KEY: str = os.getenv("KIAMIS_MOU_API_KEY", "")
NDVI_PROVIDER_API_KEY: str = os.getenv("NDVI_PROVIDER_API_KEY", "")
OPEN_METEO_BASE_URL: str = os.getenv(
    "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1"
)
CHIRPS_FIXTURE_PATH: str = os.getenv(
    "CHIRPS_FIXTURE_PATH", "fixtures/climate_wards.json"
)


# ---------------------------------------------------------------------------
# Demo / debug switches
# ---------------------------------------------------------------------------

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: Force the climate connector's UNAVAILABLE path, proving a null resolves to
#: the neutral band instead of a zero.
FORCE_NDVI_TIMEOUT: bool = _env_flag("FORCE_NDVI_TIMEOUT")

#: Artificial latency on mock connectors, so a demo feels like real partner
#: response times instead of an instant dict lookup.
DEMO_LATENCY_MS: int = int(os.getenv("DEMO_LATENCY_MS", "0"))


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def validate() -> None:
    """Fail fast on a mis-edited config rather than scoring farmers wrongly."""
    total = sum(SCORE_WEIGHTS.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"SCORE_WEIGHTS must sum to 1.0, got {total!r}")

    overlap = set(SCORE_WEIGHTS) & set(STRUCTURING_PARAMETERS)
    if overlap:
        raise ValueError(
            "structuring parameters must never be weighted into the composite "
            f"score; found {sorted(overlap)}"
        )

    if not BAND_MIN_POINTS <= NEUTRAL_BAND["points"] <= BAND_MAX_POINTS:
        raise ValueError("NEUTRAL_BAND points out of range")

    for name in ("capacity_base_kes", "climate_multiplier", "term_months_by_climate"):
        table = LOAN_STRUCTURING[name]
        missing = [p for p in range(BAND_MIN_POINTS, BAND_MAX_POINTS + 1) if p not in table]
        if missing:
            raise ValueError(f"LOAN_STRUCTURING[{name!r}] missing bands {missing}")

    unknown = set(CONNECTOR_MODE) - set(CONNECTOR_SOURCES)
    if unknown:
        raise ValueError(f"CONNECTOR_MODE has unknown sources: {sorted(unknown)}")


validate()
