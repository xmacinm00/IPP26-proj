"""
This module contains the main logic of the interpreter.

IPP: You must definitely modify this file. Bend it to your will.

Author: Ondřej Ondryáš <iondryas@fit.vut.cz>
Author:
"""

import logging
from pathlib import Path
from typing import TextIO

from lxml import etree
from lxml.etree import ParseError
from pydantic import ValidationError

from interpreter.error_codes import ErrorCode
from interpreter.exceptions import InterpreterError
from interpreter.input_model import Block, ClassDef, Method, Program
from interpreter.runtime import RuntimeNil, RuntimeObject

logger = logging.getLogger(__name__)
BUILTIN_CLASSES = {"Object", "Nil", "True", "False", "Integer", "String", "Block"}

class Interpreter:
    """
    The main interpreter class, responsible for loading the source file and executing the program.
    """

    def __init__(self) -> None:
        self.current_program: Program | None = None

    def load_program(self, source_file_path: Path) -> None:
        """
        Reads the source SOL-XML file and stores it as the target program for this interpreter.
        If any program was previously loaded, it is replaced by the new one.

        IPP: If you wish to run static checks on the program before execution, this is a good place
             to call them from.
        """
        logger.info("Opening source file: %s", source_file_path)
        try:
            xml_tree = etree.parse(source_file_path)
        except ParseError as e:
            raise InterpreterError(
                error_code=ErrorCode.INT_XML, message="Error parsing input XML"
            ) from e
        try:
            self.current_program = Program.from_xml_tree(xml_tree.getroot())  # type: ignore
        except ValidationError as e:
            raise InterpreterError(
                error_code=ErrorCode.INT_STRUCTURE, message="Invalid SOL-XML structure"
            ) from e

    def _require_program(self) -> Program:
        if self.current_program is None:
            raise InterpreterError(ErrorCode.GENERAL_OTHER, "No program loaded.")
        return self.current_program

    def _find_main_class(self, program: Program) -> ClassDef:
        for class_def in program.classes:
            if class_def.name == "Main":
                return class_def
        raise InterpreterError(ErrorCode.SEM_MAIN, "Missing class Main.")

    def _find_run_method(self, main_class: ClassDef) -> Method:
        for method in main_class.methods:
            if method.selector == "run":
                return method
        raise InterpreterError(ErrorCode.SEM_MAIN, "Missing instance method run in class Main.")

    def _build_class_table(self, program: Program) -> dict[str, ClassDef]:
        class_table: dict[str, ClassDef] = {}

        for class_def in program.classes:
            if class_def.name in class_table:
                raise InterpreterError(
                    ErrorCode.SEM_ERROR,
                    f"Duplicate class definition: {class_def.name}",
                )
            class_table[class_def.name] = class_def

        return class_table

    def _validate_inheritance(self, class_table: dict[str, ClassDef]) -> None:
        known_classes = BUILTIN_CLASSES | set(class_table.keys())

        for class_def in class_table.values():
            if class_def.parent not in known_classes:
                raise InterpreterError(
                    ErrorCode.SEM_ERROR,
                    f"Undefined parent class: {class_def.parent}",
                )

        for class_name in class_table:
            visited: set[str] = set()
            current = class_name

            while current in class_table:
                if current in visited:
                    raise InterpreterError(
                        ErrorCode.SEM_ERROR,
                        f"Inheritance cycle detected at class: {class_name}",
                    )
                visited.add(current)
                current = class_table[current].parent

    def _lookup_method(
            self,
            class_name: str,
            selector: str,
            class_table: dict[str, ClassDef],
    ) -> Method:
        current = class_name

        while current in class_table:
            class_def = class_table[current]

            for method in class_def.methods:
                if method.selector == selector:
                    return method

            current = class_def.parent

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {selector} not found for class {class_name}",
        )

    def _execute_block(self, block: Block) -> RuntimeNil:
        if block.arity != 0:
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                f"Expected zero-arity block in current execution slice, got {block.arity}",
            )

        if len(block.assigns) == 0:
            return RuntimeNil()

        raise InterpreterError(
            ErrorCode.INT_OTHER,
            "Assignments are not supported in the current execution slice.",
        )

    def execute(self, input_io: TextIO) -> None:
        """
        Executes the currently loaded program, using the provided input stream as standard input.
        """
        logger.info("Executing program")
        # MY CODE
        program = self._require_program()
        class_table = self._build_class_table(program)
        self._validate_inheritance(class_table)
        main_class = class_table.get("Main")
        if main_class is None:
            raise InterpreterError(ErrorCode.SEM_MAIN, "Missing class Main.")

        run_method = self._lookup_method("Main", "run", class_table)
        _ = self._execute_block(run_method.block)
        main_instance = RuntimeObject(class_def=main_class)
        logger.info("Instantiated %s", main_instance.class_def.name)
        logger.info("Simulating call to %s>>%s", main_class.name, run_method.selector)

        return
