"""Agrovet / M-Pesa connector -- mobile-money behaviour as cash-flow evidence.

Feeds exactly one band: Cash-Flow Stability. ``monthly_inflows_kes`` carries the
variability the band measures, and ``input_purchases_before_planting`` is the
timing signal that a farmer who buys inputs *ahead* of the season is planning
rather than scrambling.

Where the input-purchase signal comes from matters, because the obvious answer is
wrong. There is no agrovet API to call: iProcure is defunct and the sector runs on
paper and cash (PRD s8). The signal is derived from classifying the farmer's own
M-Pesa Till/Paybill counterparties -- an agrovet till paid in the weeks before
planting -- not pulled from any supplier system. ``unmapped_till_transactions``
is the honest residual of that classification: tills we could not attribute, which
is why a large residual is a small penalty rather than a large one.

SIM ATTRIBUTES MUST NOT INFLUENCE ANY BAND. The ``sim`` sub-dict merged in from
``fixtures/sim.json`` is shipped for the LIVE-MODE FRAUD CHECKS, which are a
PARALLEL WORKSTREAM, and is deliberately not read by :mod:`scoring.bands`. SIM
tenure and swap recency are fraud/velocity signals, not creditworthiness: wiring
them into a band would silently move every farmer's score and would penalise the
ordinary act of replacing a lost handset. If a future change needs them, it
belongs in the gate/fraud layer with its own explicit rule, never folded into
Cash-Flow Stability.

Passthrough of the mock payload otherwise -- :mod:`data.mock_farmers` stays the
single source of truth for every field that can move a band.
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
from data.mock_farmers import get_farmer, get_source_payload

from .base import BaseConnector

#: Resolved against this file, not the working directory, so the CLI demo enriches
#: identically wherever it is launched from.
_SIM_FIXTURE_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "fixtures" / "sim.json"
)


def _load_sim(path: Path) -> Dict[str, Any]:
    """Read the SIM fixture, degrading to ``{}`` on any failure.

    Read once at import: it is static demo data, and no band depends on it, so
    there is nothing a per-fetch read would buy. ``ValueError`` covers
    ``json.JSONDecodeError`` and a bad-encoding ``UnicodeDecodeError``.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # The fixture carries a top-level ``_note`` restating that nothing scores
    # this file; underscore keys are documented as non-data (fixtures/README.md)
    # and are dropped here so what is held in memory is only phone -> attributes.
    return {k: v for k, v in data.items() if not k.startswith("_")}


#: ``{phone_number: {SIM attributes}}``. Empty when the fixture is absent.
_SIM: Dict[str, Any] = _load_sim(_SIM_FIXTURE_PATH)


def _sim_attributes(farmer_id: str) -> Dict[str, Any]:
    """SIM attributes for this farmer's number, or ``{}`` when there are none.

    Keyed on the phone number rather than ``farmer_id`` because that is the key a
    live SIM-attribute provider actually answers on.
    """
    farmer = get_farmer(farmer_id)
    if not farmer:
        return {}
    entry = _SIM.get(str(farmer.get("phone") or ""))
    if not isinstance(entry, dict) or not entry:
        return {}
    return dict(entry)


@_connectors.register
class MockAgrovetMpesaConnector(BaseConnector):
    """M-Pesa inflow history and till classification, plus non-scoring SIM data."""

    source: ClassVar[str] = "agrovet_mpesa"
    mode: ClassVar[str] = "mock"
    provenance: ClassVar[str] = (
        "SYNTHETIC: hand-authored M-Pesa statement summaries with Till/Paybill "
        "classification (data/mock_farmers.py) plus synthetic SIM-tenure "
        "fixtures (fixtures/sim.json). No Daraja call; no real transactions. "
        "SIM fields are for live-mode fraud checks and feed no band."
    )

    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        self._simulate_latency()

        record = get_source_payload(farmer_id, self.source)
        if not record:
            # No consented M-Pesa statement for this farmer. Healthy source, no
            # record -- attaching SIM data would turn that into a record.
            return {}

        sim = _sim_attributes(farmer_id)
        if sim:
            # Nested under one key rather than flattened, so it is obvious at a
            # glance in the demo output that nothing in the band-driving top
            # level came from the SIM feed.
            record["sim"] = sim
        return record


# ---------------------------------------------------------------------------
# Future live implementation -- shape only, deliberately NOT registered.
# ---------------------------------------------------------------------------
# The live path is a consented Daraja statement pull, summarised into the same
# few fields the band reads -- the pipeline stores a summary, not a transaction
# ledger. Till classification stays server-side here; the band never sees a
# counterparty name.
#
# @_connectors.register
# class LiveAgrovetMpesaConnector(BaseConnector):
#     source = "agrovet_mpesa"
#     mode = "live"
#     provenance = "LIVE: Safaricom Daraja consented statement pull + till classification"
#
#     def fetch(self, farmer_id: str) -> Dict[str, Any]:
#         if not consent_granted(farmer_id, "mpesa"):
#             return {}             # no consent is no record, never a penalty
#         try:
#             statement = daraja_client.statement(msisdn=..., months=12)
#         except httpx.HTTPError as exc:
#             raise ConnectorUnavailableError("agrovet_mpesa", str(exc)) from exc
#         record = {
#             "monthly_inflows_kes": _monthly_inflows(statement),
#             "input_purchases_before_planting": _agrovet_till_before_planting(statement),
#             "unmapped_till_transactions": _unclassified_till_count(statement),
#         }
#         # Fraud-check payload only. Keep it OUT of the four band-driving keys
#         # above; scoring/bands.py must stay unable to reach it by accident.
#         record["sim"] = sim_provider.attributes(msisdn=...)
#         return record
