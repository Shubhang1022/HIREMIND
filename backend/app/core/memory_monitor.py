"""Memory monitoring utilities for recovery operations.

This module provides reusable helper functions to check available system memory
and determine if recovery operations should proceed. Used by both job_manager.py
and platform.py auto_resume mechanisms to prevent memory exhaustion on free-tier
instances.

Requirement 3.1: Memory-aware scheduling with logging and threshold checks.
"""

import logging
from typing import Tuple

import psutil

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_memory_stats() -> dict[str, float]:
    """Get current system memory statistics.
    
    Returns:
        Dictionary containing:
        - available_mb: Available memory in MB
        - total_mb: Total system memory in MB
        - percent_used: Percentage of memory currently used
        - free_mb: Free memory in MB (not including cached/buffered)
        
    Example:
        >>> stats = get_memory_stats()
        >>> print(f"Available: {stats['available_mb']:.2f} MB")
    """
    try:
        mem = psutil.virtual_memory()
        return {
            "available_mb": mem.available / (1024 * 1024),
            "total_mb": mem.total / (1024 * 1024),
            "percent_used": mem.percent,
            "free_mb": mem.free / (1024 * 1024),
        }
    except Exception as e:
        logger.warning("[MEMORY_MONITOR] Failed to get memory stats: %s", e)
        # Return safe defaults that won't block operations
        return {
            "available_mb": 1000.0,
            "total_mb": 1000.0,
            "percent_used": 0.0,
            "free_mb": 1000.0,
        }


def check_memory_available(
    threshold_mb: int | None = None,
    context: str = "recovery"
) -> Tuple[bool, dict[str, float]]:
    """Check if sufficient memory is available for recovery operations.
    
    Args:
        threshold_mb: Minimum required available memory in MB. 
                     If None, uses settings.FREE_MEMORY_THRESHOLD_MB.
        context: Description of the operation requesting the check (for logging)
        
    Returns:
        Tuple of (is_available, memory_stats):
        - is_available: True if available memory >= threshold, False otherwise
        - memory_stats: Dictionary with current memory statistics
        
    Example:
        >>> available, stats = check_memory_available(threshold_mb=200, context="job_recovery")
        >>> if available:
        ...     # Proceed with recovery operation
        ...     pass
        >>> else:
        ...     logger.warning("Insufficient memory: %.2f MB", stats['available_mb'])
    """
    if threshold_mb is None:
        threshold_mb = settings.FREE_MEMORY_THRESHOLD_MB
    
    stats = get_memory_stats()
    available_mb = stats["available_mb"]
    is_available = available_mb >= threshold_mb
    
    if is_available:
        logger.info(
            "[MEMORY_CHECK] %s: Memory check passed. "
            "Available: %.2f MB (threshold: %d MB, usage: %.1f%%)",
            context, available_mb, threshold_mb, stats["percent_used"]
        )
    else:
        logger.warning(
            "[MEMORY_CHECK] %s: Insufficient memory. "
            "Available: %.2f MB, Required: %d MB (usage: %.1f%%). "
            "Operation should be deferred.",
            context, available_mb, threshold_mb, stats["percent_used"]
        )
    
    return is_available, stats


def log_memory_status(context: str = "system") -> None:
    """Log current memory status with detailed breakdown.
    
    Useful for debugging and monitoring memory usage at key points
    during startup, recovery operations, or job execution.
    
    Args:
        context: Description of when/why this check is being logged
        
    Example:
        >>> log_memory_status("startup_recovery_begin")
        [MEMORY_STATUS] startup_recovery_begin: Available=450.25 MB, Total=512.00 MB, Used=12.1%
    """
    try:
        stats = get_memory_stats()
        logger.info(
            "[MEMORY_STATUS] %s: Available=%.2f MB, Total=%.2f MB, Used=%.1f%%, Free=%.2f MB",
            context,
            stats["available_mb"],
            stats["total_mb"],
            stats["percent_used"],
            stats["free_mb"]
        )
    except Exception as e:
        # Logging should never raise exceptions - fail silently
        logger.debug("[MEMORY_STATUS] Failed to log memory status for %s: %s", context, e)


def is_memory_constrained() -> bool:
    """Check if system is running in a memory-constrained environment.
    
    Uses the FREE_MEMORY_THRESHOLD_MB setting to determine if current
    available memory is below the threshold, indicating a constrained
    environment where recovery operations should be more conservative.
    
    Returns:
        True if available memory < FREE_MEMORY_THRESHOLD_MB, False otherwise
        
    Example:
        >>> if is_memory_constrained():
        ...     # Use more conservative batch sizes or defer operations
        ...     batch_size = 2
        ... else:
        ...     batch_size = 10
    """
    available, _ = check_memory_available()
    return not available
