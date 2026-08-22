"""
Run Chaos Suite Script
======================

CLI script to run the 10 RevPilot adversarial fault-injection scenarios
and report safety verification metrics.

Usage:
    python -m scripts.run_chaos_suite
"""

from __future__ import annotations

import argparse
import sys

from backend.services.chaos import ChaosSuite


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run RevPilot Chaos Engineering Suite to verify financial safety under adversarial faults."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260821,
        help="Random seed for reproducible chaos runs (default: 20260821)",
    )

    args = parser.parse_args()

    # Chaos Mode is explicitly enabled for this runner
    suite = ChaosSuite(enabled=True)
    results = suite.run_all(seed=args.seed)

    table_str = suite.format_summary_table(results)
    print("\n" + table_str + "\n")

    # Verify that ALL scenarios were 100% safe
    all_safe = all(r.safe for r in results)
    if all_safe:
        print("✅ Chaos Suite PASSED: 10/10 scenarios verified 100% financially safe.\n")
        return 0
    else:
        unsafe_count = sum(1 for r in results if not r.safe)
        print(f"❌ Chaos Suite FAILED: {unsafe_count} scenario(s) violated financial safety.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
