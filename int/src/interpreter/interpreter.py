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

from interpreter.environment import RuntimeEnvironment, RuntimeValue
from interpreter.error_codes import ErrorCode
from interpreter.exceptions import InterpreterError
from interpreter.input_model import Block, ClassDef, Expr, Literal, Method, Program, Send
from interpreter.runtime import RuntimeInteger, RuntimeNil, RuntimeObject, RuntimeString

logger = logging.getLogger(__name__)
BUILTIN_CLASSES = {"Object", "Nil", "True", "False", "Integer", "String", "Block"}

class Interpreter:
    """
    The main interpreter class, responsible for loading the source file and executing the program.
    """

    def __init__(self) -> None:
        self.current_program: Program | None = None
        self.class_table: dict[str, ClassDef] = {}

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

    def _execute_block(
        self,
        block: Block,
        env: RuntimeEnvironment | None = None,
        arguments: list[RuntimeValue] | None = None,
    ) -> RuntimeValue:
        if env is None:
            env = RuntimeEnvironment()

        if arguments is None:
            arguments = []

        if len(arguments) != block.arity:
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                f"Expected {block.arity} block arguments, got {len(arguments)}",
            )

        for parameter, argument in zip(block.parameters, arguments, strict=True):
            env.values[parameter.name] = argument

        last_value: RuntimeValue = RuntimeNil()

        for assign in block.assigns:
            value = self._evaluate_expr(assign.expr, env)
            env.values[assign.target.name] = value
            last_value = value

        return last_value

    def _evaluate_literal(self, literal: Literal) -> RuntimeValue:
        if literal.class_id == "Nil":
            return RuntimeNil()

        if literal.class_id == "Integer":
            return RuntimeInteger(int(literal.value))

        if literal.class_id == "String":
            return RuntimeString(literal.value)

        raise InterpreterError(
            ErrorCode.INT_OTHER,
            f"Literal class {literal.class_id} is not supported in the current execution slice.",
        )

    def _evaluate_expr(self, expr: Expr, env: RuntimeEnvironment) -> RuntimeValue:
        if expr.literal is not None:
            return self._evaluate_literal(expr.literal)

        if expr.var is not None:
            value = env.values.get(expr.var.name)
            if value is None:
                raise InterpreterError(
                    ErrorCode.SEM_UNDEF,
                    f"Undefined variable: {expr.var.name}",
                )
            return value

        if expr.send is not None:
            return self._evaluate_send(expr.send, env)

        raise InterpreterError(
            ErrorCode.INT_OTHER,
            "Only literals and variables are supported in the current execution slice.",
        )

    def _evaluate_send(self, send: Send, env: RuntimeEnvironment) -> RuntimeValue:
        receiver = self._evaluate_expr(send.receiver, env)

        if isinstance(receiver, (RuntimeString, RuntimeInteger)):
            return self._evaluate_builtin_send(receiver, send, env)

        if not isinstance(receiver, RuntimeObject):
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                "Only sends to supported receivers are allowed in the current execution slice.",
            )

        try:
            method = self._lookup_method(receiver.class_def.name, send.selector, self.class_table)
        except InterpreterError as e:
            if e.error_code == ErrorCode.INT_DNU:
                return self._evaluate_builtin_send(receiver, send, env)
            raise

        argument_values = [self._evaluate_expr(arg.expr, env) for arg in send.args]
        method_env = RuntimeEnvironment(values={"self": receiver})
        return self._execute_block(method.block, method_env, argument_values)

    def _validate_method_arities(self) -> None:
        for class_def in self.class_table.values():
            for method in class_def.methods:
                selector_arity = method.selector.count(":")
                if method.block.arity != selector_arity:
                    raise InterpreterError(
                        ErrorCode.SEM_ARITY,
                        f"Method {class_def.name}>>{method.selector} has mismatched block arity.",
                    )

    def _evaluate_builtin_send(
            self,
            receiver: RuntimeValue,
            send: Send,
            env: RuntimeEnvironment,
    ) -> RuntimeValue:
        argument_values = [self._evaluate_expr(arg.expr, env) for arg in send.args]

        if isinstance(receiver, RuntimeString) and send.selector == "print":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in String.",
                )
            print(receiver.value, end="")
            return receiver

        if isinstance(receiver, RuntimeInteger) and send.selector == "asString":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Integer.",
                )
            return RuntimeString(str(receiver.value))

        if isinstance(receiver, RuntimeObject) and send.selector == "asString":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Object.",
                )
            return RuntimeString("")

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in receiver.",
        )

    def execute(self, input_io: TextIO) -> None:
        """
        Executes the currently loaded program, using the provided input stream as standard input.
        """
        logger.info("Executing program")

        program = self._require_program()
        self.class_table = self._build_class_table(program)
        self._validate_inheritance(self.class_table)
        self._validate_method_arities()
        main_class = self.class_table.get("Main")
        if main_class is None:
            raise InterpreterError(ErrorCode.SEM_MAIN, "Missing class Main.")

        try:
            run_method = self._lookup_method("Main", "run", self.class_table)
        except InterpreterError as e:
            if e.error_code == ErrorCode.INT_DNU:
                raise InterpreterError(
                    ErrorCode.SEM_MAIN,
                    "Missing instance method run in class Main.",
                ) from e
            raise

        main_instance = RuntimeObject(class_def=main_class)

        method_env = RuntimeEnvironment(values={"self": main_instance})
        _ = self._execute_block(run_method.block, method_env)
        logger.info("Instantiated %s", main_instance.class_def.name)
        logger.info("Simulating call to %s>>%s", main_class.name, run_method.selector)

        return
