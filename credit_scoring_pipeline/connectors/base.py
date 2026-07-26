"""The one interface every external data source sits behind.

The whole point of this layer: a pilot launches on mock data today and flips
individual sources to live partner feeds later *without touching the scoring
logic*. That only holds if every connector obeys the same three-outcome
contract, so it is spelled out here rather than left to convention.

``fetch()`` has exactly three legal outcomes:

1. **A populated dict** -- the source has a record for this farmer.
2. **An empty dict** -- the source is healthy and has *no* record for this
   farmer (a thin-file farmer, a non-member of any co-op). The engine resolves
   this to :data:`config.NEUTRAL_BAND`.
3. **:class:`ConnectorUnavailableError`** -- the source could not be reached.
   The engine catches it, also resolves to the neutral band, and records
   ``raw_sources_used[source] = False`` for auditability.

Outcomes 2 and 3 are scored identically but reported differently, which is the
distinction a lender needs: "this farmer has no co-op history" is a fact about
the farmer, "we could not reach the co-op" is a fact about our pipeline. Neither
is ever scored as evidence of bad behaviour.

A connector must never raise anything else. Anything a live HTTP client can
throw -- timeout, 500, malformed JSON, auth failure -- is the connector's job to
catch and re-raise as ``ConnectorUnavailableError``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict

import config


class ConnectorUnavailableError(RuntimeError):
    """A data source could not be reached.

    Carries the source name so the engine can mark ``raw_sources_used`` without
    the caller having to track which connector it was talking to.
    """

    def __init__(self, source: str, reason: str = "") -> None:
        self.source = source
        self.reason = reason
        detail = f": {reason}" if reason else ""
        super().__init__(f"connector {source!r} unavailable{detail}")


class BaseConnector(ABC):
    """Abstract base for every data source.

    Subclasses set :attr:`source` and :attr:`mode` as class attributes and
    implement :meth:`fetch`.
    """

    #: Registry key -- one of :data:`config.CONNECTOR_SOURCES`.
    source: ClassVar[str] = ""

    #: ``"mock"`` or ``"live"``.
    mode: ClassVar[str] = "mock"

    #: Human-readable note on where the data actually comes from. Surfaced in
    #: the CLI demo so a reviewer can tell synthetic data from pre-cached real
    #: data at a glance.
    provenance: ClassVar[str] = ""

    @abstractmethod
    def fetch(self, farmer_id: str) -> Dict[str, Any]:
        """Return this source's record for ``farmer_id``.

        Returns ``{}`` when the source has no record. Raises
        :class:`ConnectorUnavailableError` when the source is unreachable.
        """
        raise NotImplementedError

    # -- helpers available to subclasses ------------------------------------

    @staticmethod
    def _simulate_latency() -> None:
        """Apply ``DEMO_LATENCY_MS`` so a demo feels like a real partner call."""
        delay_ms = config.DEMO_LATENCY_MS
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<{type(self).__name__} source={self.source!r} mode={self.mode!r}>"
