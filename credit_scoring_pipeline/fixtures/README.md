# `fixtures/` - partner-shaped data for mock mode

Every connector in this pipeline runs in `mock` mode by default (PRD s8: "Real
production data-sharing agreements" are a parallel workstream, not something
this codebase secures). These files hold the data that mock mode serves, in the
**shape a real partner response arrives in** rather than in an internal
convenience schema. That is the whole point: when
`CONNECTOR_MODE_<SOURCE>` flips to `live`, the payload a live feed returns
should drop into the same band functions unchanged.

## Nothing here is real observed data

All values in this directory are **synthetic**. Some files are synthetic
end-to-end (there is no real person behind `iprs.json`); others are synthetic
values sitting in the **slot where pre-cached real data would go** once a
provider credential exists. The distinction matters when reading the PRD s6
file tree, which labels the NDVI and CHIRPS files "cached real" - that describes
the *intended* production arrangement, not the contents of this repo today.

| File | Stands in for | Kind |
|---|---|---|
| `iprs.json` | ID-document read plus the IPRS match flag. Note it supplies two legs of the KYC triple only - the document-extracted ID and `iprs_match`. The registry leg is the KIAMIS `national_id`, and the typed leg is the farmer's own entry. | Synthetic end-to-end |
| `sim.json` | Telco / aggregator SIM tenure + swap lookup | Synthetic end-to-end, **and deliberately unwired** |
| `climate_wards.json` | Per-ward CHIRPS seasonal climatology, cached at boot | Synthetic values in a pre-cached-real *shape* |
| `ndvi/<parcel_id>.json` | Sentinel-2 NDVI time series per parcel, cached per parcel | Synthetic values in a pre-cached-real *shape* |

## `data/mock_farmers.py` is the source of truth

Anything in these fixtures that drives a band or a gate is **derived from**
`credit_scoring_pipeline/data/mock_farmers.py`, never asserted independently.
A contradiction between a fixture and `mock_farmers.py` is a bug in the fixture.
The coupling points are:

- `iprs.json[fid].extracted_national_id` vs the farmer's typed
  `national_id` and the KIAMIS registry `national_id`. For F001-F006 all three
  agree, so the three-way KYC check passes. For **F007** the document and the
  registry agree on `11223344` and the *typed* value (`99887766`) is the odd one
  out - which is the actual fraud story: someone typed an ID that is not the one
  on the document they presented.
- `climate_wards.json[ward].regional_exposure` and `.rainfall_deficit_pct` vs
  that farmer's `climate` payload. These two fields feed
  `band_climate_exposure`, so a mismatch would silently move a loan amount.
- `ndvi/<parcel>.json.anomaly_vs_baseline` vs that farmer's
  `climate.ndvi_anomaly`, for the same reason.

Two derived numbers are also checkable by hand, and a reviewer should check
them:

```
rainfall_deficit_pct = round((1 - season_to_date_mm / chirps_baseline_mm_season) * 100)
mean(series[].ndvi)  = baseline_mean_ndvi + anomaly_vs_baseline
```

Example, `turkwel`: `(1 - 80/210) * 100 = 61.90` -> `62`, the stated deficit,
and that ward's parcel `TUR-TKW-00038` has a series mean of `0.14` against a
`0.45` baseline, i.e. the stated `-0.31` anomaly. A near-bare-soil NDVI on a
62%-short season is the arid-Turkana story told twice, consistently.

## `sim.json` is intentionally not wired to anything

SIM tenure and swap recency are standard mobile-lending fraud and stability
signals, and a reviewer will expect to see them. They are **not** read by
`scoring/bands.py` or `scoring/engine.py`: no eligibility gate and no scored
parameter consumes this file in the MVP. Three reasons, in order of weight:

1. There is no consent category for telco metadata in PRD s3.2. Wiring it in
   needs its own logged consent event first, not a quiet addition to an
   existing one.
2. SIM age correlates with age, income and gender in ways this pilot has no
   outcome data to control for - exactly the kind of proxy the fairness
   requirement (PRD s8, Article 27) exists to keep out of the score.
3. It is not needed. F007 carries the textbook fraud pattern here (a line only
   two months old, swapped last month) and is still gated purely on the
   three-way national-ID check. The gate fires without this data.

The file therefore carries a top-level `_note` restating that. Any consumer
iterating `sim.json` must skip keys beginning with `_`.

## No ID images, ever

`iprs.json` sets `specimen: true` on every entry. Per PRD s8, national-ID
verification compares typed, document-extracted and registry values three ways
and **no selfie or ID image is retained** beyond the demo's watermarked-SPECIMEN
fixtures. `extracted_national_id` is the *result* of a document read, not a
pointer to a stored document, and there is no image path field to add one to.

## Swapping a fixture for a live feed

The fixture is never read by the scoring layer directly - only by a connector -
so a swap is a connector-and-config change with no scoring edits.

**1. Register a live connector for that source** (see
`connectors/__init__.py`): implement `BaseConnector`, set `mode = "live"`, and
translate the provider response into the same key names the fixture uses. Catch
every network failure and re-raise it as `ConnectorUnavailableError(source,
reason)` - a live feed must never raise anything else.

**2. Point it at the provider.** The relevant environment variables already
exist in `config.py`:

| Variable | Replaces |
|---|---|
| `NDVI_PROVIDER_API_KEY` | `ndvi/*.json` - Copernicus / Digital Earth Africa Sentinel-2 pulls |
| `OPEN_METEO_BASE_URL` | the `season_to_date_mm` half of `climate_wards.json` |
| `CHIRPS_FIXTURE_PATH` | the file path itself, if the cached climatology lives elsewhere. Default `fixtures/climate_wards.json`; a relative value resolves against `credit_scoring_pipeline/`, not the working directory, so the CLI demo behaves the same wherever it is launched from. An absolute path still wins. |
| `KIAMIS_MOU_ENDPOINT` / `KIAMIS_MOU_API_KEY` | the registry `national_id` half of the KYC triple |

`iprs.json` has no env var yet because no IPRS integration is contracted; add
one alongside the live connector when it is.

**3. Flip one switch:** `CONNECTOR_MODE_CLIMATE=live` (or `_KIAMIS`, etc.).
Nothing under `scoring/` changes. If the live feed is down, the engine resolves
that source to `config.NEUTRAL_BAND` and reports
`raw_sources_used[source] = false` - a farmer's session never fails because a
partner API did.

**Keep the fixtures after the swap.** They stay the fixed input the golden
tests are pinned against; a live feed's values move, and tests pinned to a
moving target stop being tests. `FORCE_NDVI_TIMEOUT=1` exercises the
unavailable path against these same files.

## Adding a farmer

Add the profile to `data/mock_farmers.py` first, then add: one `iprs.json`
entry keyed by `farmer_id`, one `sim.json` entry keyed by the phone number
exactly as written there, one `ndvi/<parcel_id>.json`, and one
`climate_wards.json` entry if the ward is new. Re-derive the two arithmetic
identities above before committing; both are cheap to get subtly wrong and
neither fails loudly.
