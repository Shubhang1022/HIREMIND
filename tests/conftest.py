"""
Shared pytest configuration and fixtures for the test suite.
"""
import pytest
import sys
import os

# Ensure the backend directory is in python path for all tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


@pytest.fixture(autouse=True)
async def reset_asyncpg_pool():
    """
    Reset the asyncpg connection pool between async tests to prevent
    event loop closed errors when each test creates a new event loop
    (pytest-asyncio 0.24.0 behavior with asyncio_mode=auto).
    """
    # Yield first (setup done before test)
    yield
    # After each test: close and reset the pool so the next test creates a fresh one
    try:
        import app.services.job_manager as jm
        if jm._asyncpg_pool is not None:
            await jm._asyncpg_pool.close()
            jm._asyncpg_pool = None
        # Also reset schema initialized flag so next test can verify schema
        jm.JobManager._schema_initialized = False
        # Reset recovery semaphore so each test gets a fresh one
        if jm.JobManager._instance is not None:
            jm.JobManager._instance._recovery_semaphore = None
    except Exception:
        pass
