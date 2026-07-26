"""Cooperative / off-taker connector -- the relational evidence base.

This is the source that does the most work for a farmer a bureau cannot see. One
co-op record feeds three parameters: Market Linkage (``delivery_tier``), Social
Collateral (``member_since`` and the delivery ratio) and, where the co-op runs an
input-credit scheme, Repayment History as a proxy -- servicing an input loan
through deductions is repayment behaviour, whether or not a bureau ever recorded
it (PRD s2).

Which makes its absence the sharpest test of the second invariant. A farmer who
belongs to no co-op returns ``{}`` here and lands on
:data:`config.NEUTRAL_BAND` for all three parameters, not the floor. F002 is the
demo profile for exactly this: the neutral resolution is worth ~32 points to
that farmer, and it is the single most important behaviour in the model.

Live rollout is deliberately staged rather than universal. KTDA is the first live
target -- it is the largest organised off-taker, already digitised at factory
level, and the pilot's tea farmers deliver through it. Every other co-op and
SACCO arrives through the give-to-get app: the co-op gets a delivery/deductions
dashboard, and in exchange the farmer can consent to sharing that history to
raise their limit. Phase 4 is optional and asynchronous for the same reason
(PRD s5.2): the offer is never blocked waiting for a co-op record to resolve.

A pure passthrough of the mock payload. There is no fixture enrichment here on
purpose -- every field the three bands read is already the partner-shaped record
in :mod:`data.mock_farmers`, which stays the single source of truth for anything
that can move a band.
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
class MockCooperativeConnector(BaseConnector):
    """Co-op membership, delivery and input-deduction history, from fixtures."""

    source: ClassVar[str] = "cooperative"
    mode: ClassVar[str] = "mock"
    provenance: ClassVar[str] = (
        "SYNTHETIC: hand-authored co-op records shaped like a KTDA factory "
        "delivery extract (data/mock_farmers.py). No live co-op feed; no real "
        "member data."
    )

    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        self._simulate_latency()
        # ``{}`` here means "not a member of any co-op", which the engine reads as
        # neutral -- distinct from an outage, which raises. See connectors.base.
        return get_source_payload(farmer_id, self.source)


# ---------------------------------------------------------------------------
# Future live implementation -- shape only, deliberately NOT registered.
# ---------------------------------------------------------------------------
# Two live shapes will eventually sit behind this one source name: a KTDA
# factory-system pull, and a give-to-get read of the co-op dashboard's own store
# for everyone else. Both normalise to the same payload the bands already read,
# so scoring/ never learns which co-op a farmer belongs to.
#
# @_connectors.register
# class LiveCooperativeConnector(BaseConnector):
#     source = "cooperative"
#     mode = "live"
#     provenance = "LIVE: KTDA factory delivery extract / give-to-get co-op store"
#
#     def fetch(self, farmer_id: str) -> Dict[str, Any]:
#         farmer = get_farmer(farmer_id)
#         if farmer is None:
#             return {}
#         try:
#             resp = httpx.get(
#                 f"{self._base_url_for(farmer)}/members",
#                 params={"national_id": farmer["national_id"]},
#                 timeout=10.0,
#             )
#             if resp.status_code == 404:
#                 return {}         # not a member: a fact, not a failure
#             resp.raise_for_status()
#             return _to_pipeline_shape(resp.json())
#         except httpx.HTTPError as exc:
#             # Phase 4 is optional and async: an unreachable co-op must resolve
#             # to the neutral band and let the offer go out, then uplift later.
#             raise ConnectorUnavailableError("cooperative", str(exc)) from exc
