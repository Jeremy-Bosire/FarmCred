"""Climate connector -- exposure evidence that sizes a loan and nothing else.

Feeds exactly one band, Climate Exposure, which is structuring-only: it sets the
amount, the term and whether index insurance is a condition. It can never fail a
farmer and never reaches the composite score. That is not an implementation
detail, it is the point -- rejecting a farmer for the weather over their ward is
explicitly out of scope (PRD s8), so the connector that supplies the weather is
kept structurally separate from the gates and the score.

This is the one source with mixed provenance, so the provenance string spells it
out. The three band-driving fields (``regional_exposure``, ``ndvi_anomaly``,
``rainfall_deficit_pct``) come from :mod:`data.mock_farmers`, which stays the
single source of truth for anything that can move a band. On top of those we
merge cached reference data -- a per-parcel Sentinel-2 NDVI series and a per-ward
CHIRPS seasonal climatology -- carried in the exact shape a live Digital Earth
Africa / KMD response arrives in, so a reviewer can see what live mode will hand
back. Each fixture states its own attribution and this connector passes that
through rather than asserting anything about it: the values are synthetic today,
sitting in the slot pre-cached real data occupies once a provider credential
exists (``fixtures/README.md``). Passing the attribution through instead of
hard-coding "real" is what stops the demo overclaiming.

Enrichment is strictly additive: :func:`_add_reference` refuses to write the
three band-driving keys even if a cached fixture disagrees with the payload. A
stale reference series is a cosmetic problem; a band that silently shifts because
a cache was refreshed is a scoring problem, and the golden numbers were
hand-verified against the payload.

``FORCE_NDVI_TIMEOUT`` makes the unavailable path reachable on demand. It is read
at fetch time, not import time, so a test can flip it and prove that a null
resolves to :data:`config.NEUTRAL_BAND` (points = 3) rather than to a zero -- the
difference between "we do not know this farmer's climate risk" and "this
farmer's climate risk is catastrophic".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, Final

import config

# Package object, not ``from connectors import register``: ``connectors/__init__``
# imports this module from the bottom of its own body, so the direct form would be
# a circular import. Reading ``register`` off the package when the class statement
# runs breaks the cycle without depending on import order.
import connectors as _connectors
from data.mock_farmers import get_farmer, get_source_payload

from .base import BaseConnector, ConnectorUnavailableError

#: ``credit_scoring_pipeline/`` -- fixtures live beside the packages, and paths are
#: resolved against it rather than the working directory so the CLI demo enriches
#: identically wherever it is launched from.
_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent.parent

#: One cached Sentinel-2 series per parcel, keyed by ``parcel_id``.
_NDVI_DIR: Path = _PACKAGE_DIR / "fixtures" / "ndvi"

#: The fields the Climate Exposure band actually reads. Enrichment may never
#: write these -- see :func:`_add_reference`.
_BAND_DRIVING_FIELDS: Final[frozenset[str]] = frozenset(
    {"regional_exposure", "ndvi_anomaly", "rainfall_deficit_pct"}
)

#: A ``parcel_id`` becomes a filename, and in live mode it arrives from a partner
#: registry rather than from this repo. Anything outside this character set is
#: refused instead of being joined onto a path.
_SAFE_PARCEL_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_NDVI_DEFAULT_SOURCE: Final[str] = "Sentinel-2 L2A (cached)"


def _read_json(path: Path) -> Any:
    """Return parsed JSON at ``path``, or ``None`` on any IO/parse failure.

    Enrichment is a nice-to-have, so every failure mode collapses to one
    outcome: a payload with no reference data. ``ValueError`` covers
    ``json.JSONDecodeError`` and a bad-encoding ``UnicodeDecodeError``. A missing
    or corrupt cache must never become an exception a farmer's session absorbs --
    the only thing allowed to raise here is a genuine source outage.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _resolve_fixture(path_str: str) -> Path:
    """Resolve a configured fixture path against the package, not the CWD.

    ``CHIRPS_FIXTURE_PATH`` is documented as a relative default (PRD s7);
    honouring it lets an operator point at a refreshed climatology without a code
    change, while an absolute override still wins.
    """
    path = Path(path_str)
    return path if path.is_absolute() else _PACKAGE_DIR / path


def _load_ward_climatology() -> Dict[str, Any]:
    """Load the per-ward CHIRPS climatology once, at boot.

    Static reference data for every ward in the footprint, so it is read once
    rather than per farmer.
    """
    data = _read_json(_resolve_fixture(config.CHIRPS_FIXTURE_PATH))
    return data if isinstance(data, dict) else {}


#: ``{ward: {climatology fields}}``. Empty when the fixture is absent.
_WARD_CLIMATOLOGY: Dict[str, Any] = _load_ward_climatology()


def _ndvi_reference(parcel_id: str) -> Dict[str, Any]:
    """Cached NDVI series for ``parcel_id``, or ``{}`` when there is none.

    Read per fetch rather than at boot because there is one file per parcel and a
    live pilot has thousands; the cheap read is the right trade against holding
    every series in memory. Tolerant of both fixture shapes a cache export
    produces -- a bare list of observations, or a dict wrapping the series with
    its provider attribution.
    """
    if not _SAFE_PARCEL_ID.match(parcel_id):
        return {}

    raw = _read_json(_NDVI_DIR / f"{parcel_id}.json")
    if isinstance(raw, list):
        series: Any = raw
        source: Any = _NDVI_DEFAULT_SOURCE
    elif isinstance(raw, dict):
        series = raw.get("ndvi_series") or raw.get("series") or raw.get("observations")
        source = raw.get("ndvi_source") or raw.get("source") or _NDVI_DEFAULT_SOURCE
    else:
        return {}

    if not series:
        return {}
    return {"ndvi_series": series, "ndvi_source": source}


def _ward_reference(ward: str) -> Dict[str, Any]:
    """Cached CHIRPS climatology for ``ward``, or ``{}`` when there is none.

    Projected down to two keys deliberately: the ward fixture also carries
    ``regional_exposure`` and ``rainfall_deficit_pct``, both of which the Climate
    Exposure band reads. Copying those across would let a refreshed climatology
    silently move a loan amount, so they are left behind here -- and refused
    again by :func:`_add_reference` if anyone reinstates them.
    """
    entry = _WARD_CLIMATOLOGY.get(ward)
    if not isinstance(entry, dict):
        return {}

    out: Dict[str, Any] = {}
    baseline = entry.get("chirps_baseline_mm_season", entry.get("baseline_mm_season"))
    if baseline is not None:
        out["chirps_baseline_mm_season"] = baseline
    season_to_date = entry.get("season_to_date_mm")
    if season_to_date is not None:
        out["season_to_date_mm"] = season_to_date
    return out


def _add_reference(record: Dict[str, Any], additions: Dict[str, Any]) -> None:
    """Merge cached reference data into ``record`` additively.

    The guard is the executable form of this module's invariant: cached reference
    data must never overwrite a band-driving field. It is unreachable given the
    keys the helpers above emit, which is exactly why it is worth keeping -- it
    is a change to those helpers, months from now, that this catches.
    """
    for key, value in additions.items():
        if key in _BAND_DRIVING_FIELDS:
            continue
        record[key] = value


@_connectors.register
class MockClimateConnector(BaseConnector):
    """Synthetic exposure fields enriched with pre-cached real NDVI/CHIRPS data."""

    source: ClassVar[str] = "climate"
    mode: ClassVar[str] = "mock"
    provenance: ClassVar[str] = (
        "MIXED PROVENANCE: band-driving exposure fields are SYNTHETIC "
        "(data/mock_farmers.py), enriched with cached reference data in the live "
        "provider response shape - Sentinel-2 NDVI series (fixtures/ndvi/) and "
        "CHIRPS ward climatology (fixtures/climate_wards.json), each carrying "
        "its own 'source' attribution. Those values are synthetic today and "
        "occupy the slot pre-cached real data will. Enrichment is additive: it "
        "cannot move a band."
    )

    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        self._simulate_latency()

        # Read from the module at call time, not bound at import: a test
        # monkeypatches this to prove a null resolves to the neutral band rather
        # than to a zero, and an import-time copy would ignore the patch.
        if config.FORCE_NDVI_TIMEOUT:
            raise ConnectorUnavailableError(
                "climate",
                "FORCE_NDVI_TIMEOUT is set: simulated NDVI provider timeout",
            )

        record = get_source_payload(farmer_id, self.source)
        if not record:
            # No climate record at all. Enriching an empty dict would turn a
            # no-record into a record built entirely from reference data.
            return {}

        farmer = get_farmer(farmer_id) or {}

        # The satellite series is keyed on the parcel, not the farmer -- the
        # geocode is what a live NDVI provider answers on.
        _add_reference(record, _ndvi_reference(str(farmer.get("parcel_id") or "")))

        # Prefer the ward on the climate payload: it is the geography the exposure
        # numbers were computed for, which is what the climatology should baseline
        # against if the profile and the payload ever disagree.
        ward = str(record.get("ward") or farmer.get("ward") or "")
        _add_reference(record, _ward_reference(ward))

        return record


# ---------------------------------------------------------------------------
# Future live implementation -- shape only, deliberately NOT registered.
# ---------------------------------------------------------------------------
# Live climate is a fan-in of three feeds with different reliability, and the
# fallback ladder is the whole design: in-season rainfall from Open-Meteo (no
# key), NDVI from Copernicus / Digital Earth Africa (falling back to the same
# cached fixtures this mock reads), and the static CHIRPS climatology. Only a
# failure that leaves NO exposure estimate at all may raise -- everything else
# degrades to cached data, because a haircut applied on stale NDVI is better than
# a farmer's session dying on a satellite outage.
#
# @_connectors.register
# class LiveClimateConnector(BaseConnector):
#     source = "climate"
#     mode = "live"
#     provenance = "LIVE: Open-Meteo in-season rainfall + Sentinel-2 NDVI, CHIRPS baseline"
#
#     def fetch(self, farmer_id: str) -> Dict[str, Any]:
#         farmer = get_farmer(farmer_id)
#         if farmer is None or not farmer.get("gps_pin"):
#             return {}          # no GPS pin yet: no record, never a penalty
#         try:
#             rainfall = openmeteo.season_to_date(farmer["gps_pin"], config.OPEN_METEO_BASE_URL)
#         except httpx.HTTPError as exc:
#             raise ConnectorUnavailableError("climate", str(exc)) from exc
#         try:
#             ndvi = dea.ndvi_series(farmer["parcel_id"], config.NDVI_PROVIDER_API_KEY)
#         except httpx.HTTPError:
#             ndvi = _ndvi_reference(farmer["parcel_id"])   # cached fallback, no raise
#         baseline = _ward_reference(farmer["ward"])
#         return {
#             "regional_exposure": _classify_exposure(rainfall, baseline, ndvi),
#             "ndvi_anomaly": _anomaly(ndvi),
#             "rainfall_deficit_pct": _deficit_pct(rainfall, baseline),
#             "ward": farmer["ward"],
#             "season": _current_season(),
#             **baseline,
#         }
