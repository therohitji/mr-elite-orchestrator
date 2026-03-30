"""
Input sanitization utilities for type safety.
Handles casting and validation of arguments before execution.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class InputSanitizer:
    """Sanitizes and validates input arguments."""

    @staticmethod
    def sanitize_int(value: Any) -> int:
        """Convert value to integer, handling common formats."""
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            # Remove currency symbols and other non-numeric characters
            cleaned = "".join(c for c in value if c.isdigit() or c == "-")
            if not cleaned:
                raise ValueError(f"Cannot convert '{value}' to integer")
            return int(cleaned)
        raise TypeError(f"Cannot convert {type(value)} to int")

    @staticmethod
    def sanitize_float(value: Any) -> float:
        """Convert value to float."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove currency symbols
            cleaned = value.strip().replace("$", "").replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                raise ValueError(f"Cannot convert '{value}' to float")
        raise TypeError(f"Cannot convert {type(value)} to float")

    @staticmethod
    def sanitize_str(value: Any) -> str:
        """Convert value to string."""
        return str(value).strip()

    @staticmethod
    def sanitize_bool(value: Any) -> bool:
        """Convert value to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "1", "on")
        return bool(value)

    @classmethod
    def sanitize_arguments(
        cls,
        arguments: Dict[str, Any],
        schema: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Sanitize all arguments against an optional schema.
        Schema format: {"arg_name": "int|float|str|bool"}
        """
        if not schema:
            return arguments

        sanitized = {}
        sanitizers = {
            "int": cls.sanitize_int,
            "float": cls.sanitize_float,
            "str": cls.sanitize_str,
            "bool": cls.sanitize_bool,
        }

        for key, expected_type in schema.items():
            if key not in arguments:
                continue

            if expected_type not in sanitizers:
                logger.warning(f"Unknown type: {expected_type}")
                sanitized[key] = arguments[key]
                continue

            try:
                sanitized[key] = sanitizers[expected_type](arguments[key])
            except (ValueError, TypeError) as e:
                logger.error(f"Sanitization failed for '{key}': {e}")
                raise

        # Include any arguments not in schema
        for key, value in arguments.items():
            if key not in sanitized:
                sanitized[key] = value

        return sanitized
