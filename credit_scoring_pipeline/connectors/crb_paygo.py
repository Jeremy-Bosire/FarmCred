"""CRB connector -- formal credit history, PAYGo tradelines included.

One payload, three consumers, and they belong to all three sides of the split:

* the active-insolvency **gate** reads ``negative_listings`` and
  ``existing_loan_balance_kes``;
* the Repayment History **band** reads the same listings, graded by size and age,
  plus ``paygo_tradeline``;
* Climate Exposure -- **structuring only** -- reads ``sunculture_owner`` as
  irrigation mitigation, because a serviced solar pump is evidence the season is
  less rain-dependent. That is the F003 story.

PAYGO TRADELINES ARRIVE INSIDE THIS RESPONSE, and there is deliberately no
separate bilateral PAYGo integration (PRD s8). M-KOPA and the other CBK-licensed
PAYGo lenders already report full-file to the bureaus, so a Metropol full-file
pull returns the asset-financing tradeline alongside conventional ones. Building
direct integrations would be duplicated plumbing for data we are already
entitled to receive, and would need one contract per lender.

Two absences that must not be confused, because both are common and only one is
about the farmer:

* ``{}`` -- no bureau record was returned for this farmer at all.
* ``{"has_crb_file": False, ...}`` -- the bureau answered and has no file. This
  is the *thin file*: invisible to bureau-only underwriting, and the reason the
  band falls back to co-op deduction proxies before it falls back to neutral.

Neither is scored as bad behaviour. This connector runs last in the session
(PRD s5.2) with explicit prior notice, so a farmer knows a bureau check is about
to happen before it does.

Straight passthrough of the mock payload -- :mod:`data.mock_farmers` is the
single source of truth for every field that can move a band or trip the gate.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict

# Package object, not ``from connectors import register``: ``connectors/__init__``
# imports this module from the bottom of its own body, so the direct form would be
# a circular import. Reading ``register`` off the package when the class statement
# runs breaks the cycle without depending on import order.
import connectors as _connectors
from data.mock_farmers import get_source_payload

from .base import BaseConnector


@_connectors.register
class MockCRBPaygoConnector(BaseConnector):
    """Bureau file, negative listings and PAYGo tradelines, from fixtures."""

    source: ClassVar[str] = "crb_paygo"
    mode: ClassVar[str] = "mock"
    provenance: ClassVar[str] = (
        "SYNTHETIC: hand-authored bureau records shaped like a Metropol "
        "full-file response, PAYGo tradelines included "
        "(data/mock_farmers.py). No live CRB pull; no real bureau data."
    )

    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        self._simulate_latency()
        return get_source_payload(farmer_id, self.source)


# ---------------------------------------------------------------------------
# Future live implementation -- shape only, deliberately NOT registered.
# ---------------------------------------------------------------------------
# One Metropol full-file call covers both conventional and PAYGo tradelines. The
# 404-vs-outage distinction is the whole reason this cannot be a thin HTTP
# wrapper: "the bureau has no file on this farmer" is the F002 case and must
# return a record saying so, while "we could not reach the bureau" must raise.
#
# @_connectors.register
# class LiveCRBPaygoConnector(BaseConnector):
#     source = "crb_paygo"
#     mode = "live"
#     provenance = "LIVE: Metropol CRB full-file pull (PAYGo tradelines included)"
#
#     def fetch(self, farmer_id: str) -> Dict[str, Any]:
#         if not consent_granted(farmer_id, "crb"):   # prior notice served first
#             return {}
#         try:
#             resp = httpx.post(
#                 f"{config.CRB_METROPOL_BASE_URL}/report",
#                 json={"national_id": ..., "report_type": "full_file"},
#                 headers={"Authorization": f"Bearer {config.CRB_METROPOL_API_KEY}"},
#                 timeout=15.0,
#             )
#             resp.raise_for_status()
#         except httpx.HTTPError as exc:
#             raise ConnectorUnavailableError("crb_paygo", str(exc)) from exc
#         report = resp.json()
#         return {
#             "has_crb_file": bool(report.get("subject_found")),
#             "negative_listings": _listings(report),      # amount_kes + months_ago
#             "existing_loan_balance_kes": _open_balance(report),
#             "paygo_tradeline": _paygo_status(report),     # from the same full file
#             "sunculture_owner": _has_solar_irrigation_tradeline(report),
#         }
