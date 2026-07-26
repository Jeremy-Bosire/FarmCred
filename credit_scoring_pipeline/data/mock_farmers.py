"""Demo farmer profiles and the synthetic partner payloads behind them.

Three profiles carry the story the PRD asks the pilot to be able to tell
(PRD s9), and four more exist purely to prove each eligibility gate hard-stops:

===== ==================================================================
F001  Clean file. Long-standing KTDA tea member, steady M-Pesa inflows, no
      adverse bureau history. Scores ~91 (Strong).
F002  Thin file. In the KIAMIS registry and has mobile money, but *no* CRB
      file and *no* co-op record at all. The three sourceless parameters
      resolve to ``NEUTRAL_BAND``, not the floor -- which is worth ~32
      points to this farmer and is the single most important behaviour in
      the model. Scores ~59 (Building).
F003  High-risk but mitigated. A small, older bureau listing that does
      *not* trip the insolvency gate; a climate-stressed ward whose
      exposure band is lifted one step by SunCulture irrigation
      ownership. Scores ~61 (Building) on a heavily haircut loan.
----- ------------------------------------------------------------------
F004  Gate demo: unfinanced value chain (macadamia).
F005  Gate demo: out of area (Turkana -- outside the serviceable footprint).
F006  Gate demo: active insolvency (KES 85,000 listed 7 months ago).
F007  Gate demo: failed KYC (typed ID disagrees with registry and IPRS).
===== ==================================================================

Data shapes here mirror the *partner* response shapes described in PRD s2, not
some internal convenience schema. That is deliberate: when a connector flips to
live, the payload a live feed returns should drop into the same band functions
unchanged.

A source mapped to ``None`` means "this source is healthy and has no record for
this farmer" -- the connector returns ``{}`` and the engine resolves it to the
neutral band. That is different from a connector *outage*, which raises
``ConnectorUnavailableError``; see :mod:`connectors.base`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: ``{farmer_id: {"farmer": {...}, "sources": {source: payload | None}}}``
MOCK_FARMERS: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------------
    # F001 -- clean file
    # ------------------------------------------------------------------
    "F001": {
        "farmer": {
            "farmer_id": "F001",
            "name": "Grace Wanjiku",
            "phone": "+254712345001",
            "national_id": "22334455",
            "county": "kericho",
            "sub_county": "ainamoi",
            "ward": "kapsoit",
            "crop": "tea",
            "land_size_band": "2_to_5_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "family_land_no_title",
            "gps_pin": {"lat": -0.3689, "lon": 35.2861},
            "parcel_id": "KER-KAP-00417",
            "gender": "female",  # fairness monitoring only -- never scored
        },
        "sources": {
            "kiamis": {
                "national_id": "22334455",
                "phone": "+254712345001",
                "county": "kericho",
                "sub_county": "ainamoi",
                "ward": "kapsoit",
                "gps_pin": {"lat": -0.3689, "lon": 35.2861},
                "land_size_acres_self_reported": 2.8,
                "land_size_acres_satellite_verified": 2.6,
                "crop": "tea",
                "secondary_crops": ["maize", "beans"],
                "livestock_units": 4,
                "input_subsidy_history": [
                    {"season": "2024_long_rains", "programme": "NFSF", "redeemed": True},
                    {"season": "2025_long_rains", "programme": "NFSF", "redeemed": True},
                ],
            },
            "cooperative": {
                "cooperative_name": "KTDA Kapsoit Tea Factory",
                "member_since": "2020-03-01",
                "delivery_tier": "high",
                "seasons_with_delivery": 10,
                "seasons_total": 12,
                "input_loan_deductions_last_12mo": 4,
            },
            "agrovet_mpesa": {
                # Tea bonus months are visibly lumpier -- that is real, and the
                # band function is tuned to tolerate seasonality.
                "monthly_inflows_kes": [
                    14500, 15200, 13800, 22400, 16100, 15600,
                    14900, 28900, 15300, 16800, 14200, 21500,
                ],
                "input_purchases_before_planting": True,
                "unmapped_till_transactions": 3,
            },
            "crb_paygo": {
                "has_crb_file": True,
                "negative_listings": [],
                "existing_loan_balance_kes": 25_000,
                "paygo_tradeline": "completed",
                "sunculture_owner": False,
            },
            "climate": {
                "regional_exposure": "moderate",
                "ndvi_anomaly": 0.04,
                "rainfall_deficit_pct": 8,
                "ward": "kapsoit",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F002 -- thin file: no CRB record, no co-op history
    # ------------------------------------------------------------------
    "F002": {
        "farmer": {
            "farmer_id": "F002",
            "name": "Peter Kimutai",
            "phone": "+254712345002",
            "national_id": "31445566",
            "county": "trans_nzoia",
            "sub_county": "kwanza",
            "ward": "keiyo",
            "crop": "maize",
            "land_size_band": "1_to_2_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "owner_occupier_no_title",
            "gps_pin": {"lat": 1.0219, "lon": 34.9714},
            "parcel_id": "TNZ-KEI-01882",
            "gender": "male",
        },
        "sources": {
            "kiamis": {
                "national_id": "31445566",
                "phone": "+254712345002",
                "county": "trans_nzoia",
                "sub_county": "kwanza",
                "ward": "keiyo",
                "gps_pin": {"lat": 1.0219, "lon": 34.9714},
                "land_size_acres_self_reported": 1.2,
                "land_size_acres_satellite_verified": 1.1,
                "crop": "maize",
                "secondary_crops": [],
                "livestock_units": 1,
                "input_subsidy_history": [],
            },
            # Not a member of any cooperative. Healthy source, no record.
            "cooperative": None,
            "agrovet_mpesa": {
                # Modest but consistent trading inflows, with one lean month.
                # This is exactly the evidence a bureau-only view cannot see.
                "monthly_inflows_kes": [
                    6200, 5800, 6500, 7100, 2100, 6800, 6300, 8900,
                ],
                "input_purchases_before_planting": False,
                "unmapped_till_transactions": 9,
            },
            # No bureau file at all -- invisible to traditional underwriting.
            "crb_paygo": {
                "has_crb_file": False,
                "negative_listings": [],
                "existing_loan_balance_kes": 0,
                "paygo_tradeline": None,
                "sunculture_owner": False,
            },
            "climate": {
                "regional_exposure": "elevated",
                "ndvi_anomaly": -0.03,
                "rainfall_deficit_pct": 18,
                "ward": "keiyo",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F003 -- high risk, mitigated
    # ------------------------------------------------------------------
    "F003": {
        "farmer": {
            "farmer_id": "F003",
            "name": "Mary Kanini",
            "phone": "+254712345003",
            "national_id": "27889900",
            "county": "meru",
            "sub_county": "buuri",
            "ward": "kibirichia",
            "crop": "french_beans",
            "land_size_band": "1_to_2_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "leasehold_informal",
            "gps_pin": {"lat": 0.0575, "lon": 37.4519},
            "parcel_id": "MER-KIB-00293",
            "gender": "female",
        },
        "sources": {
            "kiamis": {
                "national_id": "27889900",
                "phone": "+254712345003",
                "county": "meru",
                "sub_county": "buuri",
                "ward": "kibirichia",
                "gps_pin": {"lat": 0.0575, "lon": 37.4519},
                "land_size_acres_self_reported": 1.8,
                "land_size_acres_satellite_verified": 1.7,
                "crop": "french_beans",
                "secondary_crops": ["maize"],
                "livestock_units": 2,
                "input_subsidy_history": [
                    {"season": "2025_short_rains", "programme": "NFSF", "redeemed": True},
                ],
            },
            "cooperative": {
                "cooperative_name": "Kibirichia Horticultural Growers SACCO",
                "member_since": "2019-08-15",
                "delivery_tier": "mid",
                "seasons_with_delivery": 6,
                "seasons_total": 9,
                "input_loan_deductions_last_12mo": 3,
            },
            "agrovet_mpesa": {
                # Export horticulture pays lumpily -- high variance is
                # structural here, not a sign of disorder.
                "monthly_inflows_kes": [
                    9200, 24500, 7800, 18600, 6900, 21400,
                    8300, 26800, 7100, 19500, 9800, 15200,
                ],
                "input_purchases_before_planting": True,
                "unmapped_till_transactions": 22,
            },
            "crb_paygo": {
                "has_crb_file": True,
                # Small and old: a scoring signal, NOT an insolvency gate.
                "negative_listings": [{"amount_kes": 12_000, "months_ago": 19}],
                "existing_loan_balance_kes": 45_000,
                "paygo_tradeline": "current_good",
                # The mitigation: a solar irrigation pump, financed on PAYGo and
                # being serviced on time. Lifts the climate band one step.
                "sunculture_owner": True,
            },
            "climate": {
                "regional_exposure": "high",
                "ndvi_anomaly": -0.18,
                "rainfall_deficit_pct": 28,
                "ward": "kibirichia",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F004 -- gate demo: unfinanced value chain
    # ------------------------------------------------------------------
    "F004": {
        "farmer": {
            "farmer_id": "F004",
            "name": "Joseph Mwangi",
            "phone": "+254712345004",
            "national_id": "24551133",
            "county": "nakuru",
            "sub_county": "njoro",
            "ward": "mau_narok",
            "crop": "macadamia",  # not in financed_value_chains
            "land_size_band": "2_to_5_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "owner_occupier_no_title",
            "gps_pin": {"lat": -0.7833, "lon": 35.9500},
            "parcel_id": "NAK-MAU-00551",
            "gender": "male",
        },
        "sources": {
            "kiamis": {
                "national_id": "24551133",
                "phone": "+254712345004",
                "county": "nakuru",
                "sub_county": "njoro",
                "ward": "mau_narok",
                "gps_pin": {"lat": -0.7833, "lon": 35.9500},
                "land_size_acres_self_reported": 3.4,
                "land_size_acres_satellite_verified": 3.2,
                "crop": "macadamia",
                "secondary_crops": ["maize"],
                "livestock_units": 3,
                "input_subsidy_history": [],
            },
            "cooperative": None,
            "agrovet_mpesa": None,
            "crb_paygo": {
                "has_crb_file": True,
                "negative_listings": [],
                "existing_loan_balance_kes": 0,
                "paygo_tradeline": None,
                "sunculture_owner": False,
            },
            "climate": {
                "regional_exposure": "low",
                "ndvi_anomaly": 0.02,
                "rainfall_deficit_pct": 5,
                "ward": "mau_narok",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F005 -- gate demo: out of area
    # ------------------------------------------------------------------
    "F005": {
        "farmer": {
            "farmer_id": "F005",
            "name": "Ekiru Lokwang",
            "phone": "+254712345005",
            "national_id": "29001122",
            "county": "turkana",  # not in serviceable_counties
            "sub_county": "loima",
            "ward": "turkwel",
            "crop": "maize",
            "land_size_band": "under_1_acre",
            "loan_purpose": "input_finance",
            "tenure_status": "communal",
            "gps_pin": {"lat": 3.1167, "lon": 35.6000},
            "parcel_id": "TUR-TKW-00038",
            "gender": "male",
        },
        "sources": {
            "kiamis": {
                "national_id": "29001122",
                "phone": "+254712345005",
                "county": "turkana",
                "sub_county": "loima",
                "ward": "turkwel",
                "gps_pin": {"lat": 3.1167, "lon": 35.6000},
                "land_size_acres_self_reported": 0.6,
                "land_size_acres_satellite_verified": 0.5,
                "crop": "maize",
                "secondary_crops": [],
                "livestock_units": 6,
                "input_subsidy_history": [],
            },
            "cooperative": None,
            "agrovet_mpesa": None,
            "crb_paygo": None,
            "climate": {
                "regional_exposure": "severe",
                "ndvi_anomaly": -0.31,
                "rainfall_deficit_pct": 62,
                "ward": "turkwel",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F006 -- gate demo: active insolvency
    # ------------------------------------------------------------------
    "F006": {
        "farmer": {
            "farmer_id": "F006",
            "name": "Daniel Otieno",
            "phone": "+254712345006",
            "national_id": "26773311",
            "county": "nyeri",
            "sub_county": "tetu",
            "ward": "dedan_kimathi",
            "crop": "coffee",
            "land_size_band": "1_to_2_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "owner_occupier_titled",
            "gps_pin": {"lat": -0.4167, "lon": 36.9500},
            "parcel_id": "NYE-DED-00744",
            "gender": "male",
        },
        "sources": {
            "kiamis": {
                "national_id": "26773311",
                "phone": "+254712345006",
                "county": "nyeri",
                "sub_county": "tetu",
                "ward": "dedan_kimathi",
                "gps_pin": {"lat": -0.4167, "lon": 36.9500},
                "land_size_acres_self_reported": 1.6,
                "land_size_acres_satellite_verified": 1.5,
                "crop": "coffee",
                "secondary_crops": ["bananas"],
                "livestock_units": 2,
                "input_subsidy_history": [],
            },
            "cooperative": {
                "cooperative_name": "Tetu Coffee Growers FCS",
                "member_since": "2016-01-10",
                "delivery_tier": "high",
                "seasons_with_delivery": 8,
                "seasons_total": 10,
                "input_loan_deductions_last_12mo": 2,
            },
            "agrovet_mpesa": {
                "monthly_inflows_kes": [
                    11200, 10800, 12400, 11900, 10100, 13200,
                    11500, 12800, 10600, 12100, 11300, 12600,
                ],
                "input_purchases_before_planting": True,
                "unmapped_till_transactions": 5,
            },
            "crb_paygo": {
                "has_crb_file": True,
                # Large AND recent -> trips the active-insolvency gate. Note
                # this farmer is otherwise strong: the gate is structurally
                # separate from the score, and it wins.
                "negative_listings": [{"amount_kes": 85_000, "months_ago": 7}],
                "existing_loan_balance_kes": 92_000,
                "paygo_tradeline": None,
                "sunculture_owner": False,
            },
            "climate": {
                "regional_exposure": "low",
                "ndvi_anomaly": 0.06,
                "rainfall_deficit_pct": 4,
                "ward": "dedan_kimathi",
                "season": "2026_long_rains",
            },
        },
    },
    # ------------------------------------------------------------------
    # F007 -- gate demo: failed KYC (three-way ID mismatch)
    # ------------------------------------------------------------------
    "F007": {
        "farmer": {
            "farmer_id": "F007",
            "name": "Unverified Applicant",
            "phone": "+254712345007",
            # Typed ID disagrees with both the registry and IPRS.
            "national_id": "99887766",
            "county": "kiambu",
            "sub_county": "githunguri",
            "ward": "komothai",
            "crop": "dairy",
            "land_size_band": "1_to_2_acres",
            "loan_purpose": "input_finance",
            "tenure_status": "owner_occupier_no_title",
            "gps_pin": {"lat": -1.0500, "lon": 36.7500},
            "parcel_id": "KIA-KOM-00126",
            "gender": "female",
        },
        "sources": {
            "kiamis": {
                "national_id": "11223344",  # registry says someone else
                "phone": "+254712345007",
                "county": "kiambu",
                "sub_county": "githunguri",
                "ward": "komothai",
                "gps_pin": {"lat": -1.0500, "lon": 36.7500},
                "land_size_acres_self_reported": 1.4,
                "land_size_acres_satellite_verified": 1.3,
                "crop": "dairy",
                "secondary_crops": ["napier"],
                "livestock_units": 3,
                "input_subsidy_history": [],
            },
            "cooperative": None,
            "agrovet_mpesa": None,
            "crb_paygo": None,
            "climate": {
                "regional_exposure": "low",
                "ndvi_anomaly": 0.01,
                "rainfall_deficit_pct": 7,
                "ward": "komothai",
                "season": "2026_long_rains",
            },
        },
    },
}

#: The three profiles the PRD's success criteria are written against.
CANONICAL_DEMO_IDS = ("F001", "F002", "F003")

#: Profiles that exist to prove each eligibility gate hard-stops.
GATE_DEMO_IDS = ("F004", "F005", "F006", "F007")


def get_farmer(farmer_id: str) -> Optional[Dict[str, Any]]:
    """Return the Farmer record for ``farmer_id``, or ``None`` if unknown."""
    entry = MOCK_FARMERS.get(farmer_id)
    return dict(entry["farmer"]) if entry else None


def get_source_payload(farmer_id: str, source: str) -> Dict[str, Any]:
    """Return one source's synthetic payload for ``farmer_id``.

    Returns ``{}`` both for an unknown farmer and for a source explicitly
    mapped to ``None`` -- in connector terms, "healthy source, no record".
    """
    entry = MOCK_FARMERS.get(farmer_id)
    if not entry:
        return {}
    payload = entry.get("sources", {}).get(source)
    return dict(payload) if payload else {}


def all_farmer_ids() -> list[str]:
    """Every demo farmer id, canonical profiles first."""
    return [*CANONICAL_DEMO_IDS, *GATE_DEMO_IDS]
