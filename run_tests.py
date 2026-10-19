"""Run the adapted original invariant tests using only the standard library."""
from pathlib import Path
import importlib.util
import sys
import unittest

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "engine"))
spec = importlib.util.spec_from_file_location("sample_tests", root / "tests/test_surge21.py")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
suite = unittest.TestSuite(unittest.FunctionTestCase(getattr(tests, name))
                           for name in sorted(vars(tests)) if name.startswith("test_"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
