"""Scoring package: the two entry points a caller ever needs.

Everything above this layer - the CLI demo, the API service layer, the LOS
hand-off - talks to the pipeline through :func:`check_eligibility_gates` and
:func:`score_farmer` only. Band thresholds and the composite arithmetic stay
private to :mod:`scoring.bands` and :mod:`scoring.engine` so that replacing the
rules-based scorecard with a fitted model later is an internal change (PRD s8).
"""

from __future__ import annotations

from .engine import check_eligibility_gates, score_farmer

__all__ = ["check_eligibility_gates", "score_farmer"]
