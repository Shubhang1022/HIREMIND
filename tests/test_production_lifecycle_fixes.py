import pytest
import asyncio
import uuid
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the backend directory is in python path
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from app.services.job_manager import JobManager, LockResult, DBConnection

def test_db_lock_query_exception():
    """TEST B: DB lock query throws exception -> LOCK_ERROR (NOT ALREADY_HELD)"""
    async def _run():
        manager = JobManager.get_instance()
        
        # Mock DBConnection to throw an exception on fetchrow
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = Exception("Database is offline")
        
        with patch("app.services.job_manager.DBConnection") as mock_db_conn:
            mock_db_conn.return_value.__aenter__.return_value = mock_conn
            
            lock_res, job_id, owner_id = await manager.acquire_lock("11111111-1111-1111-1111-111111111111", "user1", "indexing")
            
            assert lock_res == LockResult.ERROR
            assert job_id is None
            assert "Database is offline" in owner_id

    asyncio.run(_run())

def test_duplicate_worker_lock_already_held():
    """TEST C: Actual duplicate worker -> LOCK_ALREADY_HELD"""
    async def _run():
        manager = JobManager.get_instance()
        
        from datetime import datetime, timezone, timedelta
        # Mock DBConnection to return an active job owned by another worker
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": uuid.uuid4(),
            "owner_id": "other_worker",
            "lease_expires_at": datetime.now(timezone.utc) + timedelta(seconds=60),
            "status": "processing"
        }
        
        with patch("app.services.job_manager.DBConnection") as mock_db_conn:
            mock_db_conn.return_value.__aenter__.return_value = mock_conn
            
            lock_res, job_id, owner_id = await manager.acquire_lock("22222222-2222-2222-2222-222222222222", "user1", "indexing")
            
            assert lock_res == LockResult.ALREADY_HELD
            assert owner_id == "other_worker"

    asyncio.run(_run())

def test_stale_job_recovery_aborted_missing_upload():
    """TEST I: Stale job recovery aborts if candidate upload does not exist"""
    async def _run():
        manager = JobManager.get_instance()
        
        # Mock DBConnection to return one stale job and no candidate upload
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{
            "id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "retry_count": 0,
            "failure_reason": "",
            "status": "processing"
        }]
        mock_conn.fetchrow.return_value = None  # No upload
        
        with patch("app.services.job_manager.DBConnection") as mock_db_conn, \
             patch("app.services.job_manager.JobManager.ensure_db_schema", return_value=True):
            mock_db_conn.return_value.__aenter__.return_value = mock_conn
            
            await manager.recover_interrupted_jobs()
            
            # Verify it updated the job and project status to failed in the DB
            assert mock_conn.execute.call_count >= 2
            # Verify first call set status to failed
            args1 = mock_conn.execute.call_args_list[0][0]
            assert "failed" in args1[0]
            assert "Recovery aborted" in args1[1]

    asyncio.run(_run())

def test_stale_job_recovery_succeeds_with_upload():
    """TEST I: Stale job recovery succeeds if candidate upload exists"""
    async def _run():
        manager = JobManager.get_instance()
        
        # Mock DBConnection to return one stale job and a valid candidate upload
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{
            "id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "retry_count": 0,
            "failure_reason": "",
            "status": "processing"
        }]
        mock_conn.fetchrow.return_value = {"id": uuid.uuid4()}  # Upload exists
        
        with patch("app.services.job_manager.DBConnection") as mock_db_conn, \
             patch("app.services.job_manager.JobManager.ensure_db_schema", return_value=True), \
             patch("app.services.job_manager.JobManager._safely_run_indexing_with_backoff") as mock_retry:
            mock_db_conn.return_value.__aenter__.return_value = mock_conn
            
            await manager.recover_interrupted_jobs()
            
            # Verify it set status to 'retrying' and spawned recovery task
            assert mock_conn.execute.call_count == 1
            args = mock_conn.execute.call_args_list[0][0]
            assert "retrying" in args[0]
            mock_retry.assert_called_once()

    asyncio.run(_run())
