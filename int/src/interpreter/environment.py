"""Runtime environment for local variables."""

from dataclasses import dataclass, field

from interpreter.runtime import (
    RuntimeBlock,
    RuntimeClassRef,
    RuntimeFalse,
    RuntimeInteger,
    RuntimeNil,
    RuntimeObject,
    RuntimeString,
    RuntimeTrue,
)

type RuntimeValue = (
        RuntimeObject
        | RuntimeNil
        | RuntimeInteger
        | RuntimeString
        | RuntimeBlock
        | RuntimeClassRef
        | RuntimeTrue
        | RuntimeFalse
)

@dataclass(slots=True)
class RuntimeEnvironment:
    """Represents local variables in the currently executed block."""

    values: dict[str, RuntimeValue] = field(default_factory=dict)
