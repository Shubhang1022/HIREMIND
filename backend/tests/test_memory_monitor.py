"""Unit tests for memory monitoring utilities.

Tests the memory_monitor module that provides reusable helper functions
for checking available memory and determining if recovery operations should proceed.

Requirement 3.1: Memory-aware scheduling with logging and threshold checks.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from app.core.memory_monitor import (
    get_memory_stats,
    check_memory_available,
    log_memory_status,
    is_memory_constrained,
)


class TestGetMemoryStats:
    """Tests for get_memory_stats() function."""
    
    def test_returns_memory_stats_dict(self):
        """Test that get_memory_stats returns a dictionary with expected keys."""
        stats = get_memory_stats()
        
        assert isinstance(stats, dict)
        assert "available_mb" in stats
        assert "total_mb" in stats
        assert "percent_used" in stats
        assert "free_mb" in stats
        
    def test_all_values_are_numeric(self):
        """Test that all memory stat values are numeric (float)."""
        stats = get_memory_stats()
        
        for key, value in stats.items():
            assert isinstance(value, float), f"{key} should be float, got {type(value)}"
            assert value >= 0, f"{key} should be non-negative, got {value}"
    
    @patch('app.core.memory_monitor.psutil.virtual_memory')
    def test_handles_psutil_exception_gracefully(self, mock_vm):
        """Test that exceptions from psutil are handled and safe defaults returned."""
        mock_vm.side_effect = Exception("psutil error")
        
        stats = get_memory_stats()
        
        # Should return safe defaults that won't block operations
        assert stats["available_mb"] == 1000.0
        assert stats["total_mb"] == 1000.0
        assert stats["percent_used"] == 0.0
        assert stats["free_mb"] == 1000.0
    
    @patch('app.core.memory_monitor.psutil.virtual_memory')
    def test_converts_bytes_to_megabytes(self, mock_vm):
        """Test that memory values are correctly converted from bytes to MB."""
        mock_mem = Mock()
        mock_mem.available = 512 * 1024 * 1024  # 512 MB in bytes
        mock_mem.total = 1024 * 1024 * 1024  # 1024 MB in bytes
        mock_mem.free = 256 * 1024 * 1024  # 256 MB in bytes
        mock_mem.percent = 50.0
        mock_vm.return_value = mock_mem
        
        stats = get_memory_stats()
        
        assert stats["available_mb"] == pytest.approx(512.0, rel=0.01)
        assert stats["total_mb"] == pytest.approx(1024.0, rel=0.01)
        assert stats["free_mb"] == pytest.approx(256.0, rel=0.01)
        assert stats["percent_used"] == 50.0


class TestCheckMemoryAvailable:
    """Tests for check_memory_available() function."""
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_returns_true_when_memory_above_threshold(self, mock_stats):
        """Test that function returns True when available memory exceeds threshold."""
        mock_stats.return_value = {
            "available_mb": 300.0,
            "total_mb": 512.0,
            "percent_used": 41.4,
            "free_mb": 200.0,
        }
        
        # Threshold is 100 MB by default in settings
        is_available, stats = check_memory_available(threshold_mb=100)
        
        assert is_available is True
        assert stats["available_mb"] == 300.0
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_returns_false_when_memory_below_threshold(self, mock_stats):
        """Test that function returns False when available memory is below threshold."""
        mock_stats.return_value = {
            "available_mb": 50.0,
            "total_mb": 512.0,
            "percent_used": 90.2,
            "free_mb": 30.0,
        }
        
        is_available, stats = check_memory_available(threshold_mb=100)
        
        assert is_available is False
        assert stats["available_mb"] == 50.0
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_returns_memory_stats_dict(self, mock_stats):
        """Test that function returns memory stats dictionary as second value."""
        expected_stats = {
            "available_mb": 200.0,
            "total_mb": 512.0,
            "percent_used": 60.9,
            "free_mb": 150.0,
        }
        mock_stats.return_value = expected_stats
        
        is_available, stats = check_memory_available(threshold_mb=100)
        
        assert stats == expected_stats
    
    @patch('app.core.memory_monitor.settings')
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_uses_settings_threshold_when_none_provided(self, mock_stats, mock_settings):
        """Test that function uses FREE_MEMORY_THRESHOLD_MB from settings when threshold_mb is None."""
        mock_settings.FREE_MEMORY_THRESHOLD_MB = 200
        mock_stats.return_value = {
            "available_mb": 150.0,
            "total_mb": 512.0,
            "percent_used": 70.7,
            "free_mb": 100.0,
        }
        
        is_available, stats = check_memory_available()
        
        # Should be False because 150 < 200 (settings threshold)
        assert is_available is False
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_logs_info_when_memory_available(self, mock_stats, caplog):
        """Test that INFO log is generated when memory check passes."""
        mock_stats.return_value = {
            "available_mb": 300.0,
            "total_mb": 512.0,
            "percent_used": 41.4,
            "free_mb": 200.0,
        }
        
        import logging
        with caplog.at_level(logging.INFO):
            check_memory_available(threshold_mb=100, context="test_operation")
        
        assert "[MEMORY_CHECK]" in caplog.text
        assert "test_operation" in caplog.text
        assert "Memory check passed" in caplog.text
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_logs_warning_when_memory_insufficient(self, mock_stats, caplog):
        """Test that WARNING log is generated when memory check fails."""
        mock_stats.return_value = {
            "available_mb": 50.0,
            "total_mb": 512.0,
            "percent_used": 90.2,
            "free_mb": 30.0,
        }
        
        import logging
        with caplog.at_level(logging.WARNING):
            check_memory_available(threshold_mb=100, context="test_operation")
        
        assert "[MEMORY_CHECK]" in caplog.text
        assert "test_operation" in caplog.text
        assert "Insufficient memory" in caplog.text


class TestLogMemoryStatus:
    """Tests for log_memory_status() function."""
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_logs_memory_status(self, mock_stats, caplog):
        """Test that memory status is logged with all expected information."""
        mock_stats.return_value = {
            "available_mb": 300.0,
            "total_mb": 512.0,
            "percent_used": 41.4,
            "free_mb": 200.0,
        }
        
        import logging
        with caplog.at_level(logging.INFO):
            log_memory_status("test_context")
        
        assert "[MEMORY_STATUS]" in caplog.text
        assert "test_context" in caplog.text
        assert "300.00 MB" in caplog.text  # Available
        assert "512.00 MB" in caplog.text  # Total
        assert "41.4%" in caplog.text  # Percent used
        assert "200.00 MB" in caplog.text  # Free
    
    @patch('app.core.memory_monitor.get_memory_stats')
    def test_does_not_raise_exception(self, mock_stats):
        """Test that log_memory_status never raises exceptions (logging should be safe)."""
        mock_stats.side_effect = Exception("unexpected error")
        
        # Should not raise - logging failures should be silent
        try:
            log_memory_status("test_context")
        except Exception as e:
            pytest.fail(f"log_memory_status should not raise exceptions, got: {e}")


class TestIsMemoryConstrained:
    """Tests for is_memory_constrained() function."""
    
    @patch('app.core.memory_monitor.check_memory_available')
    def test_returns_true_when_memory_below_threshold(self, mock_check):
        """Test that function returns True when available memory is below threshold."""
        mock_check.return_value = (False, {"available_mb": 50.0})
        
        result = is_memory_constrained()
        
        assert result is True
    
    @patch('app.core.memory_monitor.check_memory_available')
    def test_returns_false_when_memory_above_threshold(self, mock_check):
        """Test that function returns False when available memory exceeds threshold."""
        mock_check.return_value = (True, {"available_mb": 300.0})
        
        result = is_memory_constrained()
        
        assert result is False
    
    @patch('app.core.memory_monitor.check_memory_available')
    def test_uses_default_threshold(self, mock_check):
        """Test that function calls check_memory_available with default threshold."""
        mock_check.return_value = (True, {"available_mb": 200.0})
        
        is_memory_constrained()
        
        # Should be called with no arguments (uses settings default)
        mock_check.assert_called_once_with()


class TestMemoryMonitorIntegration:
    """Integration tests for memory monitoring in recovery scenarios."""
    
    @patch('app.core.memory_monitor.psutil.virtual_memory')
    def test_recovery_scenario_high_memory_allows_operation(self, mock_vm):
        """Test that recovery operations proceed when memory is sufficient."""
        # Simulate free-tier instance with adequate memory
        mock_mem = Mock()
        mock_mem.available = 300 * 1024 * 1024  # 300 MB available
        mock_mem.total = 512 * 1024 * 1024  # 512 MB total
        mock_mem.free = 200 * 1024 * 1024  # 200 MB free
        mock_mem.percent = 41.4
        mock_vm.return_value = mock_mem
        
        is_available, stats = check_memory_available(threshold_mb=100, context="recovery_job_test")
        
        assert is_available is True
        assert stats["available_mb"] > 100
    
    @patch('app.core.memory_monitor.psutil.virtual_memory')
    def test_recovery_scenario_low_memory_blocks_operation(self, mock_vm):
        """Test that recovery operations are blocked when memory is insufficient."""
        # Simulate free-tier instance with low memory
        mock_mem = Mock()
        mock_mem.available = 50 * 1024 * 1024  # 50 MB available
        mock_mem.total = 512 * 1024 * 1024  # 512 MB total
        mock_mem.free = 30 * 1024 * 1024  # 30 MB free
        mock_mem.percent = 90.2
        mock_vm.return_value = mock_mem
        
        is_available, stats = check_memory_available(threshold_mb=100, context="recovery_job_test")
        
        assert is_available is False
        assert stats["available_mb"] < 100
    
    @patch('app.core.memory_monitor.psutil.virtual_memory')
    def test_multiple_sequential_checks(self, mock_vm):
        """Test that multiple memory checks work correctly in sequence."""
        # First check: high memory
        mock_mem1 = Mock()
        mock_mem1.available = 300 * 1024 * 1024
        mock_mem1.total = 512 * 1024 * 1024
        mock_mem1.free = 200 * 1024 * 1024
        mock_mem1.percent = 41.4
        mock_vm.return_value = mock_mem1
        
        is_available1, _ = check_memory_available(threshold_mb=100)
        assert is_available1 is True
        
        # Second check: low memory
        mock_mem2 = Mock()
        mock_mem2.available = 50 * 1024 * 1024
        mock_mem2.total = 512 * 1024 * 1024
        mock_mem2.free = 30 * 1024 * 1024
        mock_mem2.percent = 90.2
        mock_vm.return_value = mock_mem2
        
        is_available2, _ = check_memory_available(threshold_mb=100)
        assert is_available2 is False
