import pytest
import asyncio
from unittest.mock import AsyncMock, patch
import sys
import os

# Ensure the backend directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from app.services.job_manager import get_asyncpg_pool, DBConnection

def test_pgbouncer_statement_cache_size_zero():
    """Verify that asyncpg connection pool is created with statement_cache_size=0 for PgBouncer compatibility."""
    async def _run():
        import asyncpg
        
        # Set dummy DATABASE_URL
        os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/postgres"
        
        with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
            # Reset the global pool cache if it was set
            import app.services.job_manager
            app.services.job_manager._asyncpg_pool = None
            
            try:
                await get_asyncpg_pool()
            except Exception:
                # We expect a failure or success depending on environment, but the call to create_pool is what we check
                pass
            
            mock_create_pool.assert_called_once()
            kwargs = mock_create_pool.call_args[1]
            assert kwargs.get("statement_cache_size") == 0

    asyncio.run(_run())
