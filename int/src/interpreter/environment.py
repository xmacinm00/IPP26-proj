"""Runtime environment for local variables."""

from dataclasses import dataclass, field

from interpreter.runtime import (
    RuntimeBlock,
    RuntimeInteger,
    RuntimeNil,
    RuntimeObject,
    RuntimeString,
)

type RuntimeValue = (
        RuntimeObject
        | RuntimeNil
        | RuntimeInteger
        | RuntimeString
        | RuntimeBlock
)


@dataclass(slots=True)
class RuntimeEnvironment:
    """Represents local variables in the currently executed block."""

    values: dict[str, RuntimeValue] = field(default_factory=dict)
