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

from interpreter.context import ExecutionContext
from interpreter.environment import RuntimeEnvironment, RuntimeValue
from interpreter.error_codes import ErrorCode
from interpreter.exceptions import InterpreterError
from interpreter.input_model import Block, ClassDef, Expr, Literal, Method, Program, Send
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

logger = logging.getLogger(__name__)
BUILTIN_CLASSES = {"Object", "Nil", "True", "False", "Integer", "String", "Block"}

class Interpreter:
    """
    The main interpreter class, responsible for loading the source file and executing the program.
    """

    def __init__(self) -> None:
        self.current_program: Program | None = None
        self.class_table: dict[str, ClassDef] = {}
        self.input_io: TextIO | None = None

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

    def _lookup_zero_arg_method_name_conflict(
            self,
            class_name: str,
            selector: str,
    ) -> bool:
        current = class_name

        while current in self.class_table:
            class_def = self.class_table[current]
            for method in class_def.methods:
                if method.selector == selector:
                    return True
            current = class_def.parent

        return False

    def _execute_block(
            self,
            block: Block,
            env: RuntimeEnvironment | None = None,
            arguments: list[RuntimeValue] | None = None,
            context: ExecutionContext | None = None,
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
            value = self._evaluate_expr(assign.expr, env, context)
            env.values[assign.target.name] = value
            last_value = value

        return last_value

    def _evaluate_literal(self, literal: Literal) -> RuntimeValue:
        if literal.class_id == "True":
            return RuntimeTrue()

        if literal.class_id == "False":
            return RuntimeFalse()

        if literal.class_id == "Nil":
            return RuntimeNil()

        if literal.class_id == "Integer":
            return RuntimeInteger(int(literal.value))

        if literal.class_id == "String":
            return RuntimeString(literal.value)

        if literal.class_id == "class":
            return RuntimeClassRef(literal.value)

        raise InterpreterError(
            ErrorCode.INT_OTHER,
            f"Literal class {literal.class_id} is not supported in the current execution slice.",
        )

    def _evaluate_expr(
            self,
            expr: Expr,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if expr.block is not None:
            return RuntimeBlock(block=expr.block, captured_env=env)

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
            return self._evaluate_send(expr.send, env,context)

        raise InterpreterError(
            ErrorCode.INT_OTHER,
            "Only literals and variables are supported in the current execution slice.",
        )

    def _evaluate_send(
            self,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        receiver = self._evaluate_expr(send.receiver, env, context)

        if isinstance(receiver, RuntimeClassRef):
            return self._evaluate_class_send(receiver, send, env, context)

        if isinstance(
                receiver,
                (
                        RuntimeString,
                        RuntimeInteger,
                        RuntimeBlock,
                        RuntimeTrue,
                        RuntimeFalse,
                        RuntimeNil,
                ),
        ):
            return self._evaluate_builtin_send(receiver, send, env, context)

        if not isinstance(receiver, RuntimeObject):
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                "Only sends to supported receivers are allowed in the current execution slice.",
            )

        if self._is_super_send(send):
            return self._evaluate_super_send(receiver, send, env, context)

        # builtin_result = self._try_builtin_subclass_send(receiver, send, env, context)
        # if builtin_result is not None:
        #     return builtin_result

        return self._evaluate_object_message_send(receiver, send, env, context)

    def _is_super_send(self, send: Send) -> bool:
        return send.receiver.var is not None and send.receiver.var.name == "super"

    def _try_builtin_subclass_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue | None:
        if send.selector == "identicalTo:":
            return self._evaluate_builtin_send(receiver, send, env, context)

        builtin_ancestor = self._get_builtin_ancestor(receiver.class_def.name)

        try:
            if builtin_ancestor == "Integer" and receiver.integer_value is not None:
                return self._evaluate_integer_like_object_send(receiver, send, env, context)

            if builtin_ancestor == "String" and receiver.string_value is not None:
                return self._evaluate_string_like_object_send(receiver, send, env, context)

            if builtin_ancestor == "Block" and receiver.block_value is not None:
                return self._evaluate_block_like_object_send(receiver, send, env, context)
        except InterpreterError as err:
            if err.error_code == ErrorCode.INT_DNU:
                return None
            raise

        return None

    def _evaluate_super_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if context is None:
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                "Missing execution context for super send.",
            )

        argument_values = [
            self._evaluate_expr(arg.expr, env, context)
            for arg in send.args
        ]

        try:
            method, owner_class_name = self._lookup_super_method(
                context.current_class_name,
                send.selector,
            )
        except InterpreterError as err:
            if err.error_code != ErrorCode.INT_DNU:
                raise

            try:
                return self._evaluate_builtin_send(receiver, send, env, context)
            except InterpreterError as builtin_err:
                if builtin_err.error_code != ErrorCode.INT_DNU:
                    raise

            if len(send.args) == 0:
                if send.selector in receiver.attributes:
                    return receiver.attributes[send.selector]
                raise err

            if len(send.args) == 1:
                attr_name = send.selector.removesuffix(":")
                value = argument_values[0]

                if self._lookup_zero_arg_method_name_conflict(
                        receiver.class_def.name,
                        attr_name,
                ):
                    raise InterpreterError(
                        ErrorCode.INT_INST_ATTR,
                        f"Attribute {attr_name} collides with a method.",
                    ) from err

                receiver.attributes[attr_name] = value
                return receiver

            raise err

        method_env = RuntimeEnvironment(values={"self": receiver, "super": receiver})
        method_context = ExecutionContext(current_class_name=owner_class_name)

        return self._execute_block(
            method.block,
            method_env,
            argument_values,
            method_context,
        )

    def _evaluate_object_message_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        try:
            method = self._lookup_method(
                receiver.class_def.name,
                send.selector,
                self.class_table,
            )
        except InterpreterError as err:
            if err.error_code != ErrorCode.INT_DNU:
                raise

            return self._handle_missing_object_method(receiver, send, env, context, err)

        argument_values = [
            self._evaluate_expr(arg.expr, env, context)
            for arg in send.args
        ]

        method_env = RuntimeEnvironment(values={"self": receiver, "super": receiver})
        method_context = ExecutionContext(current_class_name=receiver.class_def.name)

        return self._execute_block(
            method.block,
            method_env,
            argument_values,
            method_context,
        )

    def _handle_missing_object_method(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None,
            original_error: InterpreterError,
    ) -> RuntimeValue:
        builtin_subclass_result = self._try_builtin_subclass_send(
            receiver,
            send,
            env,
            context,
        )
        if builtin_subclass_result is not None:
            return builtin_subclass_result

        try:
            return self._evaluate_builtin_send(receiver, send, env, context)
        except InterpreterError as builtin_err:
            if builtin_err.error_code != ErrorCode.INT_DNU:
                raise

        if len(send.args) == 0:
            if send.selector in receiver.attributes:
                return receiver.attributes[send.selector]
            raise original_error

        if len(send.args) == 1:
            attr_name = send.selector.removesuffix(":")
            value = self._evaluate_expr(send.args[0].expr, env, context)

            if self._lookup_zero_arg_method_name_conflict(
                    receiver.class_def.name,
                    attr_name,
            ):
                raise InterpreterError(
                    ErrorCode.INT_INST_ATTR,
                    f"Attribute {attr_name} collides with a method.",
                ) from original_error

            receiver.attributes[attr_name] = value
            return receiver

        raise original_error



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
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        argument_values = [self._evaluate_expr(arg.expr, env, context) for arg in send.args]

        if send.selector == "identicalTo:":
            if len(argument_values) != 1:
                raise InterpreterError(
                    ErrorCode.INT_INVALID_ARG,
                    "Object identicalTo: expects one argument.",
                )

            other = argument_values[0]
            return RuntimeTrue() if receiver is other else RuntimeFalse()

        if isinstance(receiver, RuntimeObject):
            return self._evaluate_object_send(receiver, send, argument_values)

        if isinstance(receiver, (RuntimeTrue, RuntimeFalse)):
            return self._evaluate_boolean_send(receiver, send, argument_values, context)

        if isinstance(receiver, RuntimeInteger):
            return self._evaluate_integer_send(receiver, send, argument_values)

        if isinstance(receiver, RuntimeBlock):
            return self._evaluate_block_send(receiver, send, argument_values, context)

        if isinstance(receiver, RuntimeString):
            return self._evaluate_string_send(receiver, send, argument_values)

        if isinstance(receiver, RuntimeNil):
            return self._evaluate_nil_send(receiver, send, argument_values)

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in receiver.",
        )

    def _evaluate_object_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if send.selector == "equalTo:":
            if len(argument_values) != 1:
                raise InterpreterError(
                    ErrorCode.INT_INVALID_ARG,
                    "Object equalTo: expects one argument.",
                )

            other = argument_values[0]
            return RuntimeTrue() if receiver is other else RuntimeFalse()

        if send.selector == "asString":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Object.",
                )
            return RuntimeString("")

        if send.selector in {"isNumber", "isString", "isBlock", "isNil", "isBoolean"}:
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Object.",
                )
            return RuntimeFalse()

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in Object.",
        )

    def _evaluate_boolean_send(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            send: Send,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if send.selector == "asString":
            return self._evaluate_boolean_as_string(receiver, argument_values)

        if send.selector == "isBoolean":
            return self._evaluate_boolean_is_boolean(argument_values)

        if send.selector == "not":
            return self._evaluate_boolean_not(receiver, argument_values)

        if send.selector == "and:":
            return self._evaluate_boolean_and(receiver, argument_values, context)

        if send.selector == "or:":
            return self._evaluate_boolean_or(receiver, argument_values, context)

        if send.selector == "ifTrue:ifFalse:":
            return self._evaluate_boolean_if_true_if_false(
                receiver,
                argument_values,
                context,
            )

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in boolean.",
        )

    def _evaluate_boolean_as_string(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method asString not found for built-in boolean.",
            )

        return RuntimeString("true" if isinstance(receiver, RuntimeTrue) else "false")

    def _evaluate_boolean_is_boolean(
            self,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method isBoolean not found for built-in boolean.",
            )

        return RuntimeTrue()

    def _evaluate_boolean_not(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method not not found for built-in boolean.",
            )

        return RuntimeFalse() if isinstance(receiver, RuntimeTrue) else RuntimeTrue()

    def _evaluate_boolean_and(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method and: not found for built-in boolean.",
            )

        block_value = argument_values[0]
        if not isinstance(block_value, RuntimeBlock):
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method and: not found for built-in boolean.",
            )

        if isinstance(receiver, RuntimeFalse):
            return RuntimeFalse()

        block_env = RuntimeEnvironment(values=dict(block_value.captured_env.values))
        return self._execute_block(block_value.block, block_env, context=context)

    def _evaluate_boolean_or(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method or: not found for built-in boolean.",
            )

        block_value = argument_values[0]
        if not isinstance(block_value, RuntimeBlock):
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method or: not found for built-in boolean.",
            )

        if isinstance(receiver, RuntimeTrue):
            return RuntimeTrue()

        block_env = RuntimeEnvironment(values=dict(block_value.captured_env.values))
        return self._execute_block(block_value.block, block_env, context=context)

    def _evaluate_boolean_if_true_if_false(
            self,
            receiver: RuntimeTrue | RuntimeFalse,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if len(argument_values) != 2:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method ifTrue:ifFalse: not found for built-in boolean.",
            )

        true_branch = argument_values[0]
        false_branch = argument_values[1]

        if (
                not isinstance(true_branch, RuntimeBlock)
                or not isinstance(false_branch, RuntimeBlock)
        ):
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                "ifTrue:ifFalse: expects block arguments.",
            )

        chosen = true_branch if isinstance(receiver, RuntimeTrue) else false_branch
        block_env = RuntimeEnvironment(values=dict(chosen.captured_env.values))
        return self._execute_block(chosen.block, block_env, context=context)

    def _evaluate_integer_send(
            self,
            receiver: RuntimeInteger,
            send: Send,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if send.selector == "asInteger":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Integer.",
                )
            return receiver

        if send.selector == "isNumber":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Integer.",
                )
            return RuntimeTrue()

        if send.selector == "timesRepeat:":
            return self._evaluate_integer_times_repeat(receiver, argument_values, context)

        if send.selector == "equalTo:":
            return self._evaluate_integer_equal_to(receiver, argument_values)

        if send.selector == "greaterThan:":
            return self._evaluate_integer_greater_than(receiver, argument_values)

        if send.selector == "plus:":
            return self._evaluate_integer_plus(receiver, argument_values)

        if send.selector == "minus:":
            return self._evaluate_integer_minus(receiver, argument_values)

        if send.selector == "multiplyBy:":
            return self._evaluate_integer_multiply_by(receiver, argument_values)

        if send.selector == "divBy:":
            return self._evaluate_integer_div_by(receiver, argument_values)

        if send.selector == "asString":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Integer.",
                )
            return RuntimeString(str(receiver.value))

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in Integer.",
        )

    def _evaluate_integer_times_repeat(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method timesRepeat: not found for built-in Integer.",
            )

        block_value = argument_values[0]
        if not isinstance(block_value, RuntimeBlock):
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method timesRepeat: not found for built-in Integer.",
            )

        if receiver.value <= 0:
            return RuntimeNil()

        last_value: RuntimeValue = RuntimeNil()

        for i in range(1, receiver.value + 1):
            block_env = RuntimeEnvironment(values=dict(block_value.captured_env.values))
            last_value = self._execute_block(
                block_value.block,
                block_env,
                [RuntimeInteger(i)],
                context,
            )

        return last_value

    def _require_integer_argument(
            self,
            selector: str,
            argument_values: list[RuntimeValue],
    ) -> RuntimeInteger:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                f"Integer {selector} expects one Integer argument.",
            )

        other = argument_values[0]
        if not isinstance(other, RuntimeInteger):
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                f"Integer {selector} expects an Integer argument.",
            )

        return other

    def _evaluate_integer_equal_to(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("equalTo:", argument_values)
        return RuntimeTrue() if receiver.value == other.value else RuntimeFalse()

    def _evaluate_integer_greater_than(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("greaterThan:", argument_values)
        return RuntimeTrue() if receiver.value > other.value else RuntimeFalse()

    def _evaluate_integer_plus(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("plus:", argument_values)
        return RuntimeInteger(receiver.value + other.value)

    def _evaluate_integer_minus(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("minus:", argument_values)
        return RuntimeInteger(receiver.value - other.value)

    def _evaluate_integer_multiply_by(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("multiplyBy:", argument_values)
        return RuntimeInteger(receiver.value * other.value)

    def _evaluate_integer_div_by(
            self,
            receiver: RuntimeInteger,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        other = self._require_integer_argument("divBy:", argument_values)

        if other.value == 0:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                "Division by zero.",
            )

        return RuntimeInteger(receiver.value // other.value)

    def _evaluate_block_send(
            self,
            receiver: RuntimeBlock,
            send: Send,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if send.selector == "isBlock":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Block.",
                )
            return RuntimeTrue()

        if send.selector == "whileTrue:":
            if len(argument_values) != 1:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Block.",
                )

            body_block = argument_values[0]
            if not isinstance(body_block, RuntimeBlock):
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Block.",
                )

            last_value: RuntimeValue = RuntimeNil()

            while True:
                cond_value = self._execute_block(
                    receiver.block,
                    receiver.captured_env,
                    context=context
                )

                if isinstance(cond_value, RuntimeFalse):
                    return last_value

                if not isinstance(cond_value, RuntimeTrue):
                    raise InterpreterError(
                        ErrorCode.INT_OTHER,
                        "whileTrue: condition must evaluate to a boolean.",
                    )

                last_value = self._execute_block(
                    body_block.block,
                    body_block.captured_env,
                    context=context
                )

        if receiver.block.arity == 0:
            expected_selector = "value"
        else:
            expected_selector = ":".join(["value"] * receiver.block.arity) + ":"

        if send.selector != expected_selector:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                f"Method {send.selector} not found for built-in Block.",
            )

        block_env = RuntimeEnvironment(values=dict(receiver.captured_env.values))
        result = self._execute_block(
            receiver.block,
            block_env,
            argument_values,
            context,
        )

        parameter_names = {parameter.name for parameter in receiver.block.parameters}
        for name, value in block_env.values.items():
            if name not in parameter_names:
                receiver.captured_env.values[name] = value

        return result

    def _evaluate_string_send(
            self,
            receiver: RuntimeString,
            send: Send,
            argument_values: list[RuntimeValue],
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        if send.selector == "startsWith:endsBefore:":
            return self._evaluate_string_starts_with_ends_before(receiver, argument_values)

        if send.selector == "equalTo:":
            return self._evaluate_string_equal_to(receiver, argument_values)

        if send.selector == "asInteger":
            return self._evaluate_string_as_integer(receiver, argument_values)

        if send.selector == "concatenateWith:":
            return self._evaluate_string_concatenate_with(receiver, argument_values)

        if send.selector == "length":
            return self._evaluate_string_length(receiver, argument_values)

        if send.selector == "isString":
            return self._evaluate_string_is_string(argument_values)

        if send.selector == "asString":
            return self._evaluate_string_as_string(receiver, argument_values)

        if send.selector == "print":
            return self._evaluate_string_print(receiver, argument_values)

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in String.",
        )

    def _evaluate_string_starts_with_ends_before(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 2:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method startsWith:endsBefore: not found for built-in String.",
            )

        start_value = argument_values[0]
        end_value = argument_values[1]

        if (not isinstance(start_value, RuntimeInteger) or
                not isinstance(end_value, RuntimeInteger)):
            return RuntimeNil()

        start = start_value.value
        end = end_value.value

        if start <= 0 or end <= 0:
            return RuntimeNil()

        if end - start <= 0:
            return RuntimeString("")

        start_index = start - 1
        end_index = end - 1

        return RuntimeString(receiver.value[start_index:end_index])

    def _evaluate_string_equal_to(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                "String equalTo: expects one String argument.",
            )

        other = argument_values[0]
        if not isinstance(other, RuntimeString):
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                "String equalTo: expects a String argument.",
            )

        return RuntimeTrue() if receiver.value == other.value else RuntimeFalse()

    def _evaluate_string_as_integer(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method asInteger not found for built-in String.",
            )

        try:
            return RuntimeInteger(int(receiver.value))
        except ValueError:
            return RuntimeNil()

    def _evaluate_string_concatenate_with(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method concatenateWith: not found for built-in String.",
            )

        other = argument_values[0]
        if not isinstance(other, RuntimeString):
            return RuntimeNil()

        return RuntimeString(receiver.value + other.value)

    def _evaluate_string_length(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method length not found for built-in String.",
            )

        return RuntimeInteger(len(receiver.value))

    def _evaluate_string_is_string(
            self,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method isString not found for built-in String.",
            )

        return RuntimeTrue()

    def _evaluate_string_as_string(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method asString not found for built-in String.",
            )

        return receiver

    def _evaluate_string_print(
            self,
            receiver: RuntimeString,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.INT_DNU,
                "Method print not found for built-in String.",
            )

        print(receiver.value, end="")
        return receiver

    def _evaluate_nil_send(
            self,
            receiver: RuntimeNil,
            send: Send,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if send.selector == "isNil":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Nil.",
                )
            return RuntimeTrue()

        if send.selector == "asString":
            if len(argument_values) != 0:
                raise InterpreterError(
                    ErrorCode.INT_DNU,
                    f"Method {send.selector} not found for built-in Nil.",
                )
            return RuntimeString("nil")

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {send.selector} not found for built-in Nil.",
        )

    def _lookup_super_method(
            self,
            current_class_name: str,
            selector: str,
    ) -> tuple[Method, str]:
        current_class = self.class_table.get(current_class_name)
        if current_class is None:
            raise InterpreterError(
                ErrorCode.INT_OTHER,
                f"Current class {current_class_name} not found during super lookup.",
            )

        current = current_class.parent

        while current in self.class_table:
            class_def = self.class_table[current]
            for method in class_def.methods:
                if method.selector == selector:
                    return method, class_def.name
            current = class_def.parent

        raise InterpreterError(
            ErrorCode.INT_DNU,
            f"Method {selector} not found for super in class {current_class_name}.",
        )

    def _validate_block_parameter_assignment(self, block: Block) -> None:
        parameter_names = {parameter.name for parameter in block.parameters}

        for assign in block.assigns:
            if assign.target.name in parameter_names:
                raise InterpreterError(
                    ErrorCode.SEM_COLLISION,
                    f"Assignment to block parameter: {assign.target.name}",
                )

            self._validate_expr_parameter_assignment(assign.expr)

    def _validate_expr_parameter_assignment(self, expr: Expr) -> None:
        if expr.block is not None:
            self._validate_block_parameter_assignment(expr.block)
            return

        if expr.send is not None:
            self._validate_expr_parameter_assignment(expr.send.receiver)
            for arg in expr.send.args:
                self._validate_expr_parameter_assignment(arg.expr)

    def _validate_parameter_assignments(self) -> None:
        for class_def in self.class_table.values():
            for method in class_def.methods:
                self._validate_block_parameter_assignment(method.block)

    def _evaluate_class_send(
            self,
            receiver: RuntimeClassRef,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        argument_values = [self._evaluate_expr(arg.expr, env, context) for arg in send.args]

        if send.selector == "new":
            return self._evaluate_class_new(receiver, argument_values)

        if send.selector == "from:":
            return self._evaluate_class_from(receiver, argument_values)

        if send.selector == "read":
            return self._evaluate_class_read(receiver, argument_values)

        raise InterpreterError(
            ErrorCode.SEM_UNDEF,
            f"Unsupported class-side message {send.selector} for class {receiver.name}.",
        )

    def _evaluate_class_new(
            self,
            receiver: RuntimeClassRef,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.SEM_UNDEF,
                f"Unsupported class-side message new for class {receiver.name}.",
            )

        if receiver.name in self.class_table:
            class_def = self.class_table[receiver.name]
            builtin_ancestor = self._get_builtin_ancestor(receiver.name)

            if builtin_ancestor == "Integer":
                return RuntimeObject(class_def=class_def, integer_value=0)

            if builtin_ancestor == "String":
                return RuntimeObject(class_def=class_def, string_value="")

            if builtin_ancestor == "Block":
                empty_block = Block(arity=0, parameters=[], assigns=[])
                empty_env = RuntimeEnvironment()
                return RuntimeObject(
                    class_def=class_def,
                    block_value=RuntimeBlock(block=empty_block, captured_env=empty_env),
                )

            return RuntimeObject(class_def=class_def)

        if receiver.name == "Object":
            dummy_class = ClassDef(name="Object", parent="", methods=[])
            return RuntimeObject(class_def=dummy_class)

        if receiver.name == "Nil":
            return RuntimeNil()

        if receiver.name == "True":
            return RuntimeTrue()

        if receiver.name == "False":
            return RuntimeFalse()

        if receiver.name == "Integer":
            return RuntimeInteger(0)

        if receiver.name == "String":
            return RuntimeString("")

        if receiver.name == "Block":
            empty_block = Block(arity=0, parameters=[], assigns=[])
            empty_env = RuntimeEnvironment()
            return RuntimeBlock(block=empty_block, captured_env=empty_env)

        raise InterpreterError(
            ErrorCode.SEM_UNDEF,
            f"Undefined class or unsupported built-in class: {receiver.name}",
        )

    def _copy_runtime_object_attributes(
            self,
            source: RuntimeObject,
            target: RuntimeObject) -> None:
        target.attributes = dict(source.attributes)

    def _evaluate_class_from(
            self,
            receiver: RuntimeClassRef,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 1:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                f"Class-side message from: expects one argument for class {receiver.name}.",
            )

        value = argument_values[0]

        if receiver.name in self.class_table:
            return self._evaluate_user_class_from(receiver, value)

        return self._evaluate_builtin_class_from(receiver, value)

    def _evaluate_user_class_from(
            self,
            receiver: RuntimeClassRef,
            value: RuntimeValue,
    ) -> RuntimeValue:
        class_def = self.class_table[receiver.name]
        builtin_ancestor = self._get_builtin_ancestor(receiver.name)

        if builtin_ancestor == "Integer":
            return self._build_integer_like_from(class_def, receiver.name, value)

        if builtin_ancestor == "String":
            return self._build_string_like_from(class_def, receiver.name, value)

        new_obj = RuntimeObject(class_def=class_def)
        if isinstance(value, RuntimeObject):
            self._copy_runtime_object_attributes(value, new_obj)
        return new_obj

    def _evaluate_builtin_class_from(
            self,
            receiver: RuntimeClassRef,
            value: RuntimeValue,
    ) -> RuntimeValue:
        if receiver.name == "Nil":
            return RuntimeNil()

        if receiver.name == "True":
            return RuntimeTrue()

        if receiver.name == "False":
            return RuntimeFalse()

        if receiver.name == "Object":
            dummy_class = ClassDef(name="Object", parent="", methods=[])
            new_obj = RuntimeObject(class_def=dummy_class)
            if isinstance(value, RuntimeObject):
                self._copy_runtime_object_attributes(value, new_obj)
            return new_obj

        if receiver.name == "Integer":
            if not isinstance(value, RuntimeInteger):
                raise InterpreterError(
                    ErrorCode.INT_INVALID_ARG,
                    "Integer from: expects an Integer argument.",
                )
            return RuntimeInteger(value.value)

        if receiver.name == "String":
            if not isinstance(value, RuntimeString):
                raise InterpreterError(
                    ErrorCode.INT_INVALID_ARG,
                    "String from: expects a String argument.",
                )
            return RuntimeString(value.value)

        raise InterpreterError(
            ErrorCode.SEM_UNDEF,
            f"Unsupported class-side message from: for class {receiver.name}.",
        )

    def _build_integer_like_from(
            self,
            class_def: ClassDef,
            class_name: str,
            value: RuntimeValue,
    ) -> RuntimeValue:
        int_value = self._extract_integer_value(value)
        if int_value is None:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                f"{class_name} from: expects an Integer-compatible argument.",
            )

        new_obj = RuntimeObject(class_def=class_def, integer_value=int_value)
        if isinstance(value, RuntimeObject):
            self._copy_runtime_object_attributes(value, new_obj)
        return new_obj

    def _build_string_like_from(
            self,
            class_def: ClassDef,
            class_name: str,
            value: RuntimeValue,
    ) -> RuntimeValue:
        str_value = self._extract_string_value(value)
        if str_value is None:
            raise InterpreterError(
                ErrorCode.INT_INVALID_ARG,
                f"{class_name} from: expects a String-compatible argument.",
            )

        new_obj = RuntimeObject(class_def=class_def, string_value=str_value)
        if isinstance(value, RuntimeObject):
            self._copy_runtime_object_attributes(value, new_obj)
        return new_obj

    def _evaluate_class_read(
            self,
            receiver: RuntimeClassRef,
            argument_values: list[RuntimeValue],
    ) -> RuntimeValue:
        if len(argument_values) != 0:
            raise InterpreterError(
                ErrorCode.SEM_UNDEF,
                f"Unsupported class-side message read for class {receiver.name}.",
            )

        if receiver.name != "String":
            raise InterpreterError(
                ErrorCode.SEM_UNDEF,
                f"Unsupported class-side message read for class {receiver.name}.",
            )

        if self.input_io is None:
            raise InterpreterError(
                ErrorCode.GENERAL_OTHER,
                "Input stream is not available.",
            )

        line = self.input_io.readline()
        if line.endswith("\n"):
            line = line[:-1]

        return RuntimeString(line)

    def _get_builtin_ancestor(self, class_name: str) -> str | None:
        current = class_name

        while True:
            if current in BUILTIN_CLASSES:
                return current

            class_def = self.class_table.get(current)
            if class_def is None:
                return None

            current = class_def.parent

    def _extract_integer_value(self, value: RuntimeValue) -> int | None:
        if isinstance(value, RuntimeInteger):
            return value.value
        if isinstance(value, RuntimeObject) and value.integer_value is not None:
            return value.integer_value
        return None

    def _extract_string_value(self, value: RuntimeValue) -> str | None:
        if isinstance(value, RuntimeString):
            return value.value
        if isinstance(value, RuntimeObject) and value.string_value is not None:
            return value.string_value
        return None

    def _evaluate_integer_like_object_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        assert receiver.integer_value is not None
        argument_values = [self._evaluate_expr(arg.expr, env, context) for arg in send.args]

        temp_receiver = RuntimeInteger(receiver.integer_value)
        result = self._evaluate_integer_send(temp_receiver, send, argument_values, context)

        if isinstance(result, RuntimeInteger):
            return RuntimeObject(class_def=receiver.class_def, integer_value=result.value)

        return result

    def _evaluate_string_like_object_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        assert receiver.string_value is not None
        argument_values = [self._evaluate_expr(arg.expr, env, context) for arg in send.args]

        temp_receiver = RuntimeString(receiver.string_value)
        result = self._evaluate_string_send(temp_receiver, send, argument_values)

        if isinstance(result, RuntimeString):
            return RuntimeObject(class_def=receiver.class_def, string_value=result.value)

        return result

    def _evaluate_block_like_object_send(
            self,
            receiver: RuntimeObject,
            send: Send,
            env: RuntimeEnvironment,
            context: ExecutionContext | None = None,
    ) -> RuntimeValue:
        assert receiver.block_value is not None
        argument_values = [self._evaluate_expr(arg.expr, env, context) for arg in send.args]

        return self._evaluate_block_send(
            receiver.block_value,
            send,
            argument_values,
            context,
        )

    def execute(self, input_io: TextIO) -> None:
        """
        Executes the currently loaded program, using the provided input stream as standard input.
        """
        logger.info("Executing program")

        self.input_io = input_io
        program = self._require_program()
        self.class_table = self._build_class_table(program)
        self._validate_inheritance(self.class_table)
        self._validate_method_arities()
        self._validate_parameter_assignments()
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

        method_env = RuntimeEnvironment(values={"self": main_instance, "super": main_instance})
        method_context = ExecutionContext(current_class_name=main_class.name)
        _ = self._execute_block(run_method.block, method_env, context=method_context)
        logger.info("Instantiated %s", main_instance.class_def.name)
        logger.info("Simulating call to %s>>%s", main_class.name, run_method.selector)

        return
