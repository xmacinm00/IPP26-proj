"""Execution context for the currently running method/block."""

from dataclasses import dataclass


@dataclass(slots=True)
class ExecutionContext:
    """Carries method-execution metadata needed during evaluation."""

    current_class_name: str

