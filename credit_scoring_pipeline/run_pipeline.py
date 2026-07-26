"""CLI demo entrypoint -- score the shipped demo farmers and show the working.

Deliberately readable rather than terse: the point of this script is to let a
reviewer watch the three-way split happen. Gate verdict first, then the four
creditworthiness parameters that compose the score, then -- visually fenced off
-- the two parameters that size the loan and never touch the score.

    python run_pipeline.py --all --as-of 2026-07-01
    python run_pipeline.py --farmer F001 --explain
    python run_pipeline.py --farmer F003 --force-ndvi-timeout

ASCII output only. The Windows console is cp1252 and a unicode dash in a
print() is enough to crash a live demo.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import textwrap
from typing import Any, Dict, Iterable, List, Sequence

# Allow `python credit_scoring_pipeline/run_pipeline.py` from the repo root as
# well as `python run_pipeline.py` from inside the package directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from data.mock_farmers import CANONICAL_DEMO_IDS, all_farmer_ids, get_farmer  # noqa: E402
from scoring.engine import (  # noqa: E402
    composite_from_bands,
    fetch_all_records,
    score_farmer,
)

WIDTH = 78
RULE = "=" * WIDTH
THIN = "-" * WIDTH


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _wrap(text: str, indent: str = "      ") -> str:
    """Wrap a rationale string into the report's right-hand column."""
    return textwrap.fill(
        " ".join(str(text).split()),
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _bar(points: int, width: int = 5) -> str:
    """A tiny ASCII meter, so band strength is scannable at a glance."""
    filled = max(0, min(width, int(points)))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _kes(amount: Any) -> str:
    try:
        return "KES {:,}".format(int(amount))
    except (TypeError, ValueError):
        return "KES -"


def _source_state(record_present: bool, reachable: bool) -> str:
    """Distinguish "no record" from "could not reach the source".

    Both resolve to the neutral band, but they are different facts for a
    lender, so the demo must not blur them.
    """
    if not reachable:
        return "UNAVAILABLE (scored neutral)"
    return "answered" if record_present else "no record (scored neutral)"


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _print_header(farmer: Dict[str, Any]) -> None:
    print(RULE)
    print("FARMER {0}  {1}".format(farmer.get("farmer_id", "?"), farmer.get("name", "")))
    print(RULE)
    print(
        "  Location   : {0} county, {1} ward".format(
            str(farmer.get("county", "?")).replace("_", " ").title(),
            str(farmer.get("ward", "?")).replace("_", " ").title(),
        )
    )
    print(
        "  Crop       : {0}   Land band: {1}".format(
            str(farmer.get("crop", "?")).replace("_", " "),
            str(farmer.get("land_size_band", "?")).replace("_", " "),
        )
    )
    print("  Purpose    : {0}".format(str(farmer.get("loan_purpose", "?")).replace("_", " ")))
    print("  Tenure     : {0}".format(str(farmer.get("tenure_status", "?")).replace("_", " ")))


def _print_sources(result: Dict[str, Any], records: Dict[str, Any] | None) -> None:
    used = result.get("raw_sources_used", {})
    print("")
    print("  DATA SOURCES")
    for source in config.CONNECTOR_SOURCES:
        reachable = bool(used.get(source, False))
        present = bool((records or {}).get(source)) if records is not None else reachable
        print("    {0:<16} {1}".format(source, _source_state(present, reachable)))


def _print_gates(result: Dict[str, Any]) -> None:
    print("")
    if result.get("eligible"):
        print("  ELIGIBILITY  : PASS -- all four hard gates cleared")
        return

    print("  ELIGIBILITY  : FAIL -- scoring did not run")
    print("")
    for failure in result.get("gate_failures", []):
        print("    [{0}]".format(failure.get("code", "?")))
        print(_wrap(failure.get("reason", ""), indent="      "))
        detail = failure.get("detail")
        if detail:
            print(_wrap("operator detail: " + str(detail), indent="        "))
        print("")
    print("  No score, no bands and no loan offer are produced for an ineligible")
    print("  applicant. The gates sit outside the score by design.")


def _print_scored_parameters(result: Dict[str, Any]) -> None:
    bands = result.get("parameter_bands", {})
    print("")
    print("  CREDITWORTHINESS -- the four parameters that compose the score")
    print("  " + THIN[2:])
    for name in config.SCORE_WEIGHTS:
        band = bands.get(name, {})
        print(
            "    {0} {1}/5  {2:<26} weight {3:.0%}".format(
                _bar(band.get("points", 0)),
                band.get("points", "?"),
                str(band.get("label", "")),
                config.SCORE_WEIGHTS[name],
            )
        )
        print("      {0}".format(name))
        print(_wrap(band.get("why", "")))
        print("")

    print(
        "  COMPOSITE SCORE : {0} / 100   tier: {1}".format(
            result.get("composite_score_0_100"), result.get("score_tier")
        )
    )


def _print_explain(result: Dict[str, Any]) -> None:
    """Show the composite arithmetic so a reviewer can check it by hand."""
    bands = result.get("parameter_bands", {})
    print("")
    print("  COMPOSITE ARITHMETIC")
    total = 0.0
    for name, weight in config.SCORE_WEIGHTS.items():
        points = bands.get(name, {}).get("points", 0)
        contribution = weight * points
        total += contribution
        print(
            "    {0:<22} {1:.2f} x {2} = {3:.4f}".format(
                name, weight, points, contribution
            )
        )
    print("    {0:<22} weighted points = {1:.4f}".format("", total))
    print(
        "    map 1..5 onto 0..100:  ({0:.4f} - 1) / 4 * 100 = {1}".format(
            total, result.get("composite_score_0_100")
        )
    )
    print("    (the neutral band, 3 points, lands on exactly 50.0)")


def _print_structuring(result: Dict[str, Any]) -> None:
    print("")
    print("  " + THIN[2:])
    print("  LOAN STRUCTURING -- separate from the score, cannot reject a farmer")
    print("  " + THIN[2:])
    for name in config.STRUCTURING_PARAMETERS:
        band = result.get(name, {})
        print(
            "    {0} {1}/5  {2:<26} {3}".format(
                _bar(band.get("points", 0)),
                band.get("points", "?"),
                str(band.get("label", "")),
                name,
            )
        )
        print(_wrap(band.get("why", "")))
        print("")

    loan = result.get("loan_structuring", {})
    print("    Maximum loan   : {0}".format(_kes(loan.get("max_loan_amount_kes"))))
    print("    Term           : {0} months".format(loan.get("term_months")))
    print(
        "    Index insurance: {0}".format(
            "MANDATORY" if loan.get("insurance_mandatory") else "not required"
        )
    )
    print("")
    print(_wrap(loan.get("rationale", ""), indent="    "))


def _print_levers(result: Dict[str, Any]) -> None:
    levers = result.get("improvement_levers", [])
    if not levers:
        return
    print("")
    print("  HOW TO MOVE UP NEXT SEASON")
    for lever in levers:
        print(
            "    + {0} (up to +{1} band point)".format(
                lever.get("parameter", "?"), lever.get("points_available", "?")
            )
        )
        print(_wrap(lever.get("lever", ""), indent="        "))


def _print_farmer_report(
    farmer: Dict[str, Any],
    result: Dict[str, Any],
    explain: bool,
    records: Dict[str, Any] | None = None,
) -> None:
    _print_header(farmer)
    _print_sources(result, records)
    _print_gates(result)
    if result.get("eligible"):
        _print_scored_parameters(result)
        if explain:
            _print_explain(result)
        _print_structuring(result)
        _print_levers(result)
    print("")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(results: Sequence[tuple[str, Dict[str, Any]]]) -> None:
    print(RULE)
    print("SUMMARY")
    print(RULE)
    print(
        "  {0:<6} {1:<10} {2:>7}  {3:<14} {4:<16} {5}".format(
            "ID", "ELIGIBLE", "SCORE", "TIER", "MAX LOAN", "TERM"
        )
    )
    print("  " + THIN[2:])
    for farmer_id, result in results:
        if result.get("eligible"):
            loan = result.get("loan_structuring", {})
            print(
                "  {0:<6} {1:<10} {2:>7}  {3:<14} {4:<16} {5} mo".format(
                    farmer_id,
                    "yes",
                    result.get("composite_score_0_100"),
                    str(result.get("score_tier")),
                    _kes(loan.get("max_loan_amount_kes")),
                    loan.get("term_months"),
                )
            )
        else:
            codes = ", ".join(g.get("code", "?") for g in result.get("gate_failures", []))
            print(
                "  {0:<6} {1:<10} {2:>7}  {3}".format(farmer_id, "NO", "-", codes)
            )


def _print_neutral_band_note(results: Sequence[tuple[str, Dict[str, Any]]]) -> None:
    """Quantify the neutral-band rule using F002, computed live.

    Nothing here is hardcoded: change a weight in config and this note moves
    with it, which is the only way a claim like this stays honest.
    """
    thin = dict(results).get("F002")
    if not thin or not thin.get("eligible"):
        return

    bands = thin.get("parameter_bands", {})
    actual = thin.get("composite_score_0_100")

    # The counterfactual: score every source-less parameter at the floor
    # instead of the midpoint.
    floor_bands = {}
    sourceless = []
    for name in config.SCORE_WEIGHTS:
        points = bands.get(name, {}).get("points")
        if points == config.NEUTRAL_BAND["points"]:
            floor_bands[name] = {"points": config.BAND_MIN_POINTS}
            sourceless.append(name)
        else:
            floor_bands[name] = {"points": points}
    if not sourceless:
        return

    floor_score = composite_from_bands(floor_bands)

    print("")
    print(RULE)
    print("WHY THE NEUTRAL BAND MATTERS")
    print(RULE)
    print(
        _wrap(
            "F002 is a thin-file farmer: no credit-bureau record and no cooperative "
            "history at all. Three of the four scored parameters have no data behind "
            "them ({0}). Scoring those at the neutral midpoint rather than the worst "
            "band is the difference between two very different decisions.".format(
                ", ".join(sourceless)
            ),
            indent="  ",
        )
    )
    print("")
    print("    absence scored as NEUTRAL (what this system does) : {0}".format(actual))
    print("    absence scored as the WORST band                  : {0}".format(floor_score))
    print("    difference                                        : {0:.1f} points".format(
        float(actual) - float(floor_score)
    ))
    print("")
    print(
        _wrap(
            "A farmer is invisible to a bureau because nobody has lent to them, not "
            "because they defaulted. Penalising that absence is what locks a "
            "creditworthy smallholder out permanently.",
            indent="  ",
        )
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="Score the shipped demo farmers through the FarmCred pipeline.",
    )
    parser.add_argument(
        "--farmer", action="append", metavar="ID",
        help="score one farmer (repeatable); defaults to all demo farmers",
    )
    parser.add_argument("--all", action="store_true", help="score every demo farmer")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    parser.add_argument(
        "--as-of", metavar="YYYY-MM-DD",
        help="pin the scoring date, so tenure maths is reproducible (default: today)",
    )
    parser.add_argument(
        "--explain", action="store_true",
        help="show the composite arithmetic term by term",
    )
    parser.add_argument(
        "--force-ndvi-timeout", action="store_true",
        help="force the climate connector to fail, proving a null scores neutral",
    )
    parser.add_argument(
        "--latency-ms", type=int, metavar="N",
        help="simulate partner response latency on every mock connector",
    )
    return parser.parse_args(argv)


def _resolve_ids(args: argparse.Namespace) -> List[str]:
    if args.farmer and not args.all:
        return list(args.farmer)
    return all_farmer_ids()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.as_of:
        try:
            as_of = _dt.date.fromisoformat(args.as_of)
        except ValueError:
            print("error: --as-of must be YYYY-MM-DD, got {0!r}".format(args.as_of),
                  file=sys.stderr)
            return 1
    else:
        as_of = _dt.date.today()

    # These are demo switches, so mutating config at runtime is the intended
    # path rather than a workaround -- the connectors read them at fetch time.
    if args.force_ndvi_timeout:
        config.FORCE_NDVI_TIMEOUT = True
    if args.latency_ms is not None:
        config.DEMO_LATENCY_MS = args.latency_ms

    farmer_ids = _resolve_ids(args)
    unknown = [fid for fid in farmer_ids if get_farmer(fid) is None]
    if unknown:
        print("error: unknown farmer id(s): {0}".format(", ".join(unknown)), file=sys.stderr)
        print("       known ids: {0}".format(", ".join(all_farmer_ids())), file=sys.stderr)
        return 1

    results: List[tuple[str, Dict[str, Any]]] = []
    records_by_id: Dict[str, Dict[str, Any]] = {}
    try:
        for farmer_id in farmer_ids:
            farmer = get_farmer(farmer_id)
            # Fetch once and hand the records to the engine, so the report can
            # tell "source had no record" apart from "source was unreachable".
            # raw_sources_used alone cannot express that difference.
            records, used = fetch_all_records(farmer_id)
            records_by_id[farmer_id] = records
            results.append(
                (farmer_id, score_farmer(farmer, records, used, as_of=as_of))
            )
    except Exception as exc:  # a connector outage is NOT this; that degrades
        print("internal error while scoring: {0}: {1}".format(type(exc).__name__, exc),
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([r for _, r in results], indent=2, default=str))
        return 0

    print("")
    print(RULE)
    print("FarmCred -- smallholder credit scoring, rules-based scorecard")
    print("scored as of {0}   connector modes: {1}".format(
        as_of.isoformat(),
        ", ".join("{0}={1}".format(k, v) for k, v in config.CONNECTOR_MODE.items()),
    ))
    if config.FORCE_NDVI_TIMEOUT:
        print("NOTE: climate connector forced UNAVAILABLE for this run")
    print(RULE)
    print("")

    for farmer_id, result in results:
        _print_farmer_report(
            get_farmer(farmer_id), result, args.explain, records_by_id.get(farmer_id)
        )

    if len(results) > 1:
        _print_summary(results)
        if any(fid in CANONICAL_DEMO_IDS for fid, _ in results):
            _print_neutral_band_note(results)
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
