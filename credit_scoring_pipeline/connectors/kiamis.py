"""KIAMIS registry connector -- the corroborating source.

KIAMIS is the only feed that can independently confirm what the farmer typed at
Registration, so it earns its place twice over: it supplies the registry leg of
the three-way KYC check and the county used by the out-of-area gate, and it
supplies the acreage that sizes the loan through the Production Capacity band.
Note which list it is *not* on -- nothing here reaches the composite score.

Identity evidence is merged in from ``fixtures/iprs.json`` rather than living in
:mod:`data.mock_farmers` because in live mode it does not come from the registry
query at all: ``extracted_national_id`` comes from document extraction and
``iprs_match`` from a separate IPRS lookup. Assembling the same
``identity_verification`` sub-dict here means the engine's KYC gate reads one
shape in both modes, and the mock -> live flip stays a config change.

The important behaviour in this module is a refusal: when the fixture file or
the farmer's entry is missing, ``identity_verification`` is omitted entirely
instead of being filled with nulls. A KYC gate must trip on *disagreeing*
evidence, never on *absent* evidence -- a farmer whose IPRS lookup never
resolved has failed nothing, and a null that reads as a mismatch would reject
them (PRD s2).

Per PRD s8 no ID image is retained: the fixtures carry a ``specimen`` flag on
watermarked demo documents, and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Final

# Package object, not ``from connectors import register``: ``connectors/__init__``
# imports this module from the bottom of its own body, so the direct form would be
# a circular import. Reading ``register`` off the package when the class statement
# runs breaks the cycle without depending on import order.
import connectors as _connectors
from data.mock_farmers import get_source_payload

from .base import BaseConnector

#: Resolved against this file, not the working directory -- the CLI demo must
#: enrich identically whether it is launched from the repo root or elsewhere.
_IPRS_FIXTURE_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "fixtures" / "iprs.json"
)

#: The evidence the three-way KYC gate compares. Projected explicitly so an
#: unrelated field added to the fixture cannot leak into the scoring path.
_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "extracted_national_id",
    "iprs_match",
    "iprs_name",
    "document_type",
    "specimen",
)


def _load_iprs(path: Path) -> Dict[str, Any]:
    """Read the synthetic IPRS fixture, degrading to ``{}`` on any failure.

    Read once at import because it is a static demo file, and because a
    per-fetch read would let a transient disk error look like a farmer-specific
    identity problem. ``ValueError`` covers ``json.JSONDecodeError`` and a
    bad-encoding ``UnicodeDecodeError``: a corrupt fixture must degrade to "no
    identity evidence", never crash a score.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


#: ``{farmer_id: {identity fields}}``. Empty when the fixture is absent.
_IPRS: Dict[str, Any] = _load_iprs(_IPRS_FIXTURE_PATH)


def _identity_evidence(farmer_id: str) -> Dict[str, Any]:
    """Identity evidence for ``farmer_id``, or ``{}`` when there is none.

    Fields absent from the entry stay absent rather than becoming ``None``, for
    the same reason the whole sub-dict is omitted when the entry is missing.
    """
    entry = _IPRS.get(farmer_id)
    if not isinstance(entry, dict) or not entry:
        return {}
    return {field: entry[field] for field in _IDENTITY_FIELDS if field in entry}


@_connectors.register
class MockKIAMISConnector(BaseConnector):
    """KIAMIS registry record plus assembled identity evidence, from fixtures."""

    source: ClassVar[str] = "kiamis"
    mode: ClassVar[str] = "mock"
    provenance: ClassVar[str] = (
        "SYNTHETIC: hand-authored KIAMIS-shaped registry records "
        "(data/mock_farmers.py) merged with synthetic ID-verification fixtures "
        "(fixtures/iprs.json). No live registry query; no real personal data."
    )

    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        self._simulate_latency()

        record = get_source_payload(farmer_id, self.source)
        if not record:
            # Unknown farmer, or a source explicitly mapped to None: healthy
            # source, no record. Attaching identity evidence to an empty dict
            # would turn a no-record into a record.
            return {}

        identity = _identity_evidence(farmer_id)
        if identity:
            # ``get_source_payload`` already returns a shallow copy, so adding a
            # key cannot write back into the module-level demo data.
            record["identity_verification"] = identity
        return record


# ---------------------------------------------------------------------------
# Future live implementation -- shape only, deliberately NOT registered.
# ---------------------------------------------------------------------------
# There is nothing to point this at until the Ministry of Agriculture / KALRO
# MoU lands (PRD s8), so it stays commented out: registering a class that raises
# on every call would make CONNECTOR_MODE_KIAMIS=live look supported when it is
# not. Uncommenting it and setting CONNECTOR_MODE_KIAMIS=live is the whole
# migration -- scoring/ does not change.
#
# @_connectors.register
# class LiveKIAMISConnector(BaseConnector):
#     source = "kiamis"
#     mode = "live"
#     provenance = "LIVE: KIAMIS registry query (KIAMIS_MOU_ENDPOINT)"
#
#     def fetch(self, farmer_id: str) -> Dict[str, Any]:
#         farmer = get_farmer(farmer_id)          # national ID + GPS to query on
#         if farmer is None:
#             return {}
#         try:
#             resp = httpx.get(
#                 f"{config.KIAMIS_MOU_ENDPOINT}/farmers",
#                 params={"national_id": farmer["national_id"]},
#                 headers={"X-API-Key": config.KIAMIS_MOU_API_KEY},
#                 timeout=10.0,
#             )
#             if resp.status_code == 404:
#                 return {}                       # not registered: NOT a failure
#             resp.raise_for_status()
#             record = _to_pipeline_shape(resp.json())
#         except httpx.HTTPError as exc:          # timeout, 5xx, bad JSON, auth
#             raise ConnectorUnavailableError("kiamis", str(exc)) from exc
#         # Live identity evidence: document extraction + a separate IPRS call.
#         # Both are omitted on failure, exactly as the mock omits them, so an
#         # IPRS outage can never present as a KYC mismatch.
#         evidence = _extract_document(farmer_id) | _iprs_lookup(farmer)
#         if evidence:
#             record["identity_verification"] = evidence
#         return record
