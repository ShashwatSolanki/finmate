import sys
import unittest
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
tests_dir = backend_dir / "tests"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(tests_dir) not in sys.path:
    sys.path.insert(0, str(tests_dir))

def run_tests():
    print("=" * 60)
    print("Running FinMate Auth & Regression Test Suites")
    print("=" * 60)
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    auth_patterns = [
        "test_auth*.py",
        "test_email_verification*.py",
    ]
    
    for pattern in auth_patterns:
        discovered = loader.discover(start_dir=str(tests_dir), pattern=pattern, top_level_dir=str(tests_dir))
        suite.addTests(discovered)
            
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    print(f"Tests run: {result.testsRun}, Errors: {len(result.errors)}, Failures: {len(result.failures)}")
    print("=" * 60)
    
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    sys.exit(run_tests())
