"""Runtime data structures for the SOL26 interpreter."""

from dataclasses import dataclass, field

from interpreter.input_model import ClassDef


@dataclass(slots=True)
class RuntimeObject:
    """Represents a runtime instance of a user-defined class."""

    class_def: ClassDef
    attributes: dict[str, object] = field(default_factory=dict)

@dataclass(slots=True)
class RuntimeNil:
    """Represents the built-in nil object."""

@dataclass(slots=True)
class RuntimeInteger:
    """Represents a built-in integer object."""

    value: int

@dataclass(slots=True)
class RuntimeString:
    """Represents a built-in string object."""

    value: str
