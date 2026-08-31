#!/usr/bin/env python3
"""
Script to run preservation tests independently with proper event loop management.
This avoids fixture-related async issues.
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))

from tests.test_startup_recovery_concurrency_limit import (
    test_preservation_job_completion_updates_database,
)


async def run_all_tests():
    """Run all preservation tests sequentially."""
    print("\n" + "="*80)
    print("RUNNING PRESERVATION TESTS")
    print("="*80 + "\n")
    
    results = {}
    
    # Test 1: Job Completion Updates Database
    print("\n[1/4] Running test_preservation_job_completion_updates_database...")
    try:
        await test_preservation_job_completion_updates_database()
        results['test_preservation_job_completion_updates_database'] = 'PASSED'
        print("✓ PASSED\n")
    except Exception as e:
        results['test_preservation_job_completion_updates_database'] = f'FAILED: {e}'
        print(f"✗ FAILED: {e}\n")
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    passed = sum(1 for v in results.values() if v == 'PASSED')
    total = len(results)
    for test_name, status in results.items():
        symbol = "✓" if status == "PASSED" else "✗"
        print(f"{symbol} {test_name}: {status}")
    print(f"\nTotal: {passed}/{total} passed")
    print("="*80 + "\n")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
