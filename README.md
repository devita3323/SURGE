# SURGE — selected stock-research code sample

SURGE is a stock research project. This sample contains its existing SURGE 2.1 daily price/volume sequence evaluator and tests of evidence boundaries. It evaluates supplied data; it does not execute trades or establish an investment edge.

## Verified scope

The 13 adapted original invariant tests passed during preparation with Python 3.13.2 and again on October 19, 2026 with Python 3.12.14. They check that:

- Catalyst, insider, or short-interest inputs alone do not manufacture price/volume confirmation.
- Expansion and confirmation are separate transitions; failed and extended sequences cannot retain a clean entry label.
- Missing volume is reported as unscreened, and missing or uncertain float prevents clean squeeze eligibility.
- Public open interest cannot populate a signed-dealer-gamma field.
- The separate public-time validator rejects future-public evidence.
- Criteria have a reproducible content hash, and hard blockers preserve the underlying sequence state while impairing eligibility.

These tests use generated fictional bars and records. Passing them does not validate predictive performance, calibrate probabilities, or establish returns.

## Architecture

`engine/app/radar/surge21.py` retains the existing evaluator, result dataclasses, float-sensitivity assessment, gamma-evidence guard, public-time validator and optional local CSV reader. It reads versioned criteria from `method/SURGE_2.1_CRITERIA.json` and produces structured receipts with state, coverage, warnings and gaps, without a master score.

Two dependency files are excerpts: `Bar` from the original exit-replay module, and `true_ranges`/`atr` from the technicals module. No trading replay, personal trading commentary, provider adapter, research database, API service or UI is included. The larger source tree has a Python/FastAPI service, web UI, storage and provider modules; their operation is outside this review.

## Kyle's role and AI assistance

Kyle is the project owner identified for this portfolio. The criteria file describes owner-adopted proposed priors with validation pending. This does not establish which individual wrote every function or the exact division of original work between Kyle and AI tools. Kyle should confirm that history before describing his contributions in the application.

Codex selected existing evaluator code, extracted only its required math/model dependencies, adapted the existing pytest exception checks to standard-library unittest, added an isolated runner, and wrote this documentation. The evaluator and criteria were not improved or tuned to enhance the portfolio.

## Setup and reproducible tests

Use Python 3.10+; the verified interpreter was Python 3.13.2. From this folder:

```sh
python -I -S -B run_tests.py
```

Expected result: `Ran 13 tests` and `OK`. The sample uses only the Python standard library. `-I -S` isolates startup from environment/site packages; `-B` avoids bytecode caches. No install, key, `.env`, saved market data or network connection is needed. The runner imports only the included sample files. The optional CSV reader is not invoked by these tests.

## Honest limitations

- All numerical thresholds remain proposed, unvalidated priors. Labels such as `CLEAN_CONFLUENCE` describe this program's rules, not probabilities or validated quality rankings.
- No held-out performance, backtest returns, prospective success rate, calibration, live data quality, or full application behavior was established.
- `validate_public_times` is a separate function. `evaluate` does not itself enforce that every bar and external fact was available at the chosen as-of time. Callers must establish point-in-time inputs; passing the timestamp test does not prove absence of look-ahead bias throughout the project.
- Missing float blocks the squeeze subsystem's clean eligibility; the broader classifier can still use other corroborators. Those outputs must be read separately.
- Market/sector residuals are explicitly absent. Calendar fixtures use synthetic daily sequences rather than an exchange-calendar model.
- The criteria file retains settings for wider-project modules not included here; their presence does not mean those modules were tested or implemented by this sample.
- Arbitrary external CSV paths are supported by the retained reader. This sample's tests use no private data and do not exercise that reader.

No open-source license has been selected. This repository is a selected portfolio code sample; public availability does not grant an open-source license.
