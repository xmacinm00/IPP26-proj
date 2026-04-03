"""Runtime environment for local variables."""

from dataclasses import dataclass, field

from interpreter.runtime import RuntimeInteger, RuntimeNil, RuntimeObject, RuntimeString

type RuntimeValue = RuntimeObject | RuntimeNil | RuntimeInteger | RuntimeString


@dataclass(slots=True)
class RuntimeEnvironment:
    """Represents local variables in the currently executed block."""

    values: dict[str, RuntimeValue] = field(default_factory=dict)
