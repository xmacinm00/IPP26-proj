"""Runtime data structures for the SOL26 interpreter."""

from dataclasses import dataclass, field

from interpreter.input_model import ClassDef


@dataclass(slots=True)
class RuntimeObject:
    """Represents a runtime instance of a user-defined class."""

    class_def: ClassDef
    attributes: dict[str, object] = field(default_factory=dict)
