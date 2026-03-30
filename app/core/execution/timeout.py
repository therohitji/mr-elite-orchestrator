"""
Timeout utilities for execution safety.
Implements hard 30-second timeout limit per the Phase 4 spec.
"""

import logging
import signal
import functools
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default timeout in seconds (per Phase 4 spec)
DEFAULT_TIMEOUT = 30


class TimeoutException(Exception):
    """Raised when execution exceeds timeout."""

    pass


def timeout_handler(signum, frame):
    """Signal handler for timeout."""
    raise TimeoutException("Execution timeout")


def with_timeout(timeout_seconds: int = DEFAULT_TIMEOUT) -> Callable:
    """
    Decorator to apply timeout to a function.
    Uses Unix signal alarm (Linux/Unix only).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Set the signal handler and timeout
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout_seconds)

            try:
                result = func(*args, **kwargs)
                # Cancel the alarm
                signal.alarm(0)
                return result
            except TimeoutException:
                logger.error(
                    f"Function {func.__name__} exceeded timeout ({timeout_seconds}s)"
                )
                raise
            finally:
                signal.alarm(0)

        return wrapper

    return decorator


class TimeoutManager:
    """Manages execution timeouts for tools and agents."""

    @staticmethod
    def execute_with_timeout(
        func: Callable,
        args: tuple = (),
        kwargs: dict = None,
        timeout_seconds: int = DEFAULT_TIMEOUT,
    ) -> Any:
        """
        Execute a function with timeout protection.

        Args:
            func: Function to execute
            args: Positional arguments
            kwargs: Keyword arguments
            timeout_seconds: Timeout limit in seconds

        Returns:
            Function result

        Raises:
            TimeoutException: If execution exceeds timeout
        """
        kwargs = kwargs or {}

        @with_timeout(timeout_seconds)
        def _execute():
            return func(*args, **kwargs)

        try:
            return _execute()
        except TimeoutException:
            logger.error(f"Timeout executing {func.__name__}")
            raise


# Global instance
timeout_manager = TimeoutManager()
