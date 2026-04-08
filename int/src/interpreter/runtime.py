"""Runtime data structures for the SOL26 interpreter."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from interpreter.input_model import Block, ClassDef

if TYPE_CHECKING:
    from interpreter.environment import RuntimeEnvironment, RuntimeValue

@dataclass(slots=True)
class RuntimeObject:
    """Represents a runtime instance of a user-defined class."""

    class_def: ClassDef
    attributes: dict[str, RuntimeValue] = field(default_factory=dict)
    integer_value: int | None = None
    string_value: str | None = None
    block_value: RuntimeBlock | None = None

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

@dataclass(slots=True)
class RuntimeBlock:
    """Represents a block object together with its captured environment."""

    block: Block
    captured_env: RuntimeEnvironment

@dataclass(slots=True)
class RuntimeClassRef:
    """Represents a reference to a class by name."""

    name: str

@dataclass(slots=True)
class RuntimeTrue:
    """Represents the built-in true object."""

@dataclass(slots=True)
class RuntimeFalse:
    """Represents the built-in false object."""
