# Selection and sanitization

- `engine/app/radar/surge21.py`: existing evaluator source, with normalized line endings only.
- `method/SURGE_2.1_CRITERIA.json`: existing criteria, retaining its explicit unvalidated-prior labels. No tuning was performed.
- `engine/app/backtest/exits.py`: only the `Bar` dataclass, its validation and required imports retained. Other replay code and private-context commentary omitted.
- `engine/app/services/prices/technicals.py`: only `true_ranges` and `atr` and their imports retained.
- `tests/test_surge21.py`: all thirteen original test functions retained. `pytest.raises` replaced with `unittest.TestCase.assertRaises`; generated fixtures remain unchanged.
- `run_tests.py` and minimal `__init__.py` files: new offline runner and package scaffolding with no startup side effects.

Excluded: credentials, `.env` variants, API/provider and AI integrations, notifications, launchers and services, private research databases, articles, attachments, holdings/trade records, retained prices and source data, original audit/reference documents, unrelated tests, full dependency environments, binary assets, caches, original Git metadata and history.

Private source paths and extraction hashes are retained separately in the parent review manifest. This selection is deliberately smaller than the app and contains no newly built product feature.
