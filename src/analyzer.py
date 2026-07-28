"""Calculate maintainability metrics for one Python source document."""

from __future__ import annotations

import ast
import io
import json
import sys
import tokenize
from dataclasses import dataclass
from typing import Literal, TypedDict, cast


MetricName = Literal["cyclomatic", "cognitive", "nesting", "functionLength", "parameters"]


class RangeJson(TypedDict):
    """Represent a zero-based editor source range in the JSON contract."""

    startLine: int
    startColumn: int
    endLine: int
    endColumn: int


class MetricJson(TypedDict):
    """Represent one function-level metric in the JSON contract."""

    name: MetricName
    value: int
    threshold: int
    ranges: list[RangeJson]
    details: list[str]


class FunctionJson(TypedDict):
    """Represent one uniquely identified function in the JSON contract."""

    id: str
    name: str
    qualifiedName: str
    line: int
    column: int
    metrics: list[MetricJson]


class SummaryJson(TypedDict):
    """Represent an aggregated metric for the active file."""

    name: MetricName
    maximum: int
    average: float
    threshold: int
    violatingFunctions: int
    maximumFunctionId: str | None
    ranges: list[RangeJson]


class AnalysisJson(TypedDict):
    """Represent the complete analyzer response."""

    file: str
    metrics: list[SummaryJson]
    functions: list[FunctionJson]
    error: str | None


@dataclass(frozen=True)
class Thresholds:
    """Store configured thresholds used to classify metric values.

    Keeping thresholds in one typed value makes the analyzer contract explicit.
    """

    cyclomatic: int
    cognitive: int
    nesting: int
    function_length: int
    parameters: int


@dataclass(frozen=True)
class FunctionSpec:
    """Store a function node and its scope-aware identity.

    Qualified identities prevent duplicate local function names from becoming ambiguous in the dashboard.
    """

    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualified_name: str
    is_method: bool
    is_staticmethod: bool


def source_range(node: ast.AST) -> RangeJson:
    """Convert an AST node into a zero-based editor range.

    The editor needs precise locations so dashboard selections can decorate code.
    """
    return {
        "startLine": max(getattr(node, "lineno", 1) - 1, 0),
        "startColumn": getattr(node, "col_offset", 0),
        "endLine": max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, 0),
        "endColumn": getattr(node, "end_col_offset", 0),
    }


def block_range(body: list[ast.stmt]) -> RangeJson | None:
    """Return the source range spanning a statement block.

    Block ranges let nesting decorations point at the actual deepest block instead of the whole function.
    """
    if not body:
        return None
    first = source_range(body[0])
    last = source_range(body[-1])
    return {
        "startLine": first["startLine"],
        "startColumn": first["startColumn"],
        "endLine": last["endLine"],
        "endColumn": last["endColumn"],
    }


def is_default_match_case(case: ast.match_case) -> bool:
    """Identify a wildcard match case that does not add a decision point.

    Python represents wildcard cases as a MatchAs pattern with no nested pattern or capture name.
    """
    pattern = case.pattern
    return isinstance(pattern, ast.MatchAs) and pattern.pattern is None and pattern.name is None


class FunctionCollector(ast.NodeVisitor):
    """Collect functions while preserving class and local-function scope."""

    def __init__(self) -> None:
        """Initialize the collected function list.

        A dedicated visitor ensures nested definitions are found without flattening their identities.
        """
        self.functions: list[FunctionSpec] = []
        self._prefix = ""
        self._parent_kind = "module"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Collect methods and nested functions declared in a class.

        Class scope is retained so methods are reported as ClassName.methodName.
        """
        prefix = getattr(self, "_prefix", "")
        parent_kind = getattr(self, "_parent_kind", "module")
        if parent_kind == "function":
            qualified_name = f"{prefix}.<locals>.{node.name}"
        elif prefix:
            qualified_name = f"{prefix}.{node.name}"
        else:
            qualified_name = node.name
        self._visit_body(node.body, qualified_name, "class", True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Collect a synchronous function and definitions inside its body.

        Local functions receive a `<locals>` qualifier so duplicate names remain distinguishable.
        """
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Collect an asynchronous function and definitions inside its body.

        Async functions use the same metric contract as synchronous functions.
        """
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Register one function and recurse into its nested definitions.

        Scope metadata is captured once so all metric visitors use the same identity and receiver rules.
        """
        parent_prefix = getattr(self, "_prefix", "")
        parent_kind = getattr(self, "_parent_kind", "module")
        if parent_kind == "function":
            qualified_name = f"{parent_prefix}.<locals>.{node.name}"
        elif parent_prefix:
            qualified_name = f"{parent_prefix}.{node.name}"
        else:
            qualified_name = node.name
        is_staticmethod = any(
            isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
            or isinstance(decorator, ast.Attribute) and decorator.attr == "staticmethod"
            for decorator in node.decorator_list
        )
        self.functions.append(FunctionSpec(node, qualified_name, parent_kind == "class", is_staticmethod))
        self._visit_body(node.body, qualified_name, "function", False)

    def _visit_body(self, body: list[ast.stmt], prefix: str, kind: str, is_class: bool) -> None:
        """Visit a scope body with temporary qualification context.

        The temporary context lets ordinary statements expose definitions nested inside branches and loops.
        """
        previous_prefix = getattr(self, "_prefix", "")
        previous_kind = getattr(self, "_parent_kind", "module")
        self._prefix = prefix
        self._parent_kind = "class" if is_class else kind
        for statement in body:
            self.visit(statement)
        self._prefix = previous_prefix
        self._parent_kind = previous_kind


def collect_functions(tree: ast.Module) -> list[FunctionSpec]:
    """Collect all functions with qualified names and method metadata.

    A scope-aware collection prevents nested definitions from being mistaken for anonymous duplicates.
    """
    collector = FunctionCollector()
    for statement in tree.body:
        collector.visit(statement)
    return collector.functions


class CyclomaticVisitor(ast.NodeVisitor):
    """Calculate Python cyclomatic complexity using Radon-compatible decisions."""

    def __init__(self) -> None:
        """Initialize the decision count at one.

        Every function has at least one linearly independent path.
        """
        self.value = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop at nested synchronous functions.

        Nested scopes are analyzed independently and must not inflate the parent function.
        """
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Stop at nested asynchronous functions.

        Nested scopes are analyzed independently and must not inflate the parent function.
        """
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Stop at nested classes.

        Class bodies represent a separate scope for maintainability reporting.
        """
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Stop at lambda bodies.

        Lambda control flow is kept out of the containing named function.
        """
        return

    def visit_If(self, node: ast.If) -> None:
        """Count an if or elif decision and visit its branches.

        Each conditional branch adds one independent path.
        """
        self.value += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """Count a conditional expression.

        Ternary expressions introduce a second execution path.
        """
        self.value += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """Count a for loop and its optional else path.

        Radon treats loop else blocks as an additional path.
        """
        self.value += 1 + int(bool(node.orelse))
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Count an async for loop and its optional else path.

        Async iteration has the same path structure as synchronous iteration.
        """
        self.value += 1 + int(bool(node.orelse))
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """Count a while loop and its optional else path.

        Loop completion and break behavior create a separate else path.
        """
        self.value += 1 + int(bool(node.orelse))
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """Count exception handlers and a try else path.

        Finally is unconditional and does not add a cyclomatic path.
        """
        self.value += len(node.handlers) + int(bool(node.orelse))
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Count a with block as one decision point.

        Context-manager exit behavior is treated as a possible exceptional path by Radon.
        """
        self.value += 1
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Count an async with block as one decision point.

        Async context management has the same decision behavior as with.
        """
        self.value += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        """Count an assertion decision.

        An assertion can either continue or raise, creating two paths.
        """
        self.value += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Count additional operands in a boolean expression.

        Every and/or operator adds an independent short-circuit path.
        """
        self.value += max(len(node.values) - 1, 0)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Count a comprehension generator and its filters.

        Comprehensions are equivalent to compact loop structures with optional conditions.
        """
        self.value += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        """Count non-default match cases and visit their expressions.

        A wildcard case is the match equivalent of an unconditional else branch.
        """
        self.value += sum(not is_default_match_case(case) for case in node.cases)
        self.generic_visit(node)


def cyclomatic_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calculate the independent path count for one function.

    The visitor is isolated to the function so nested definitions cannot pollute its result.
    """
    visitor = CyclomaticVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.value


class CognitiveVisitor(ast.NodeVisitor):
    """Calculate a documented Sonar-style cognitive complexity estimate."""

    def __init__(self, function_name: str) -> None:
        """Initialize score, nesting, and contributor ranges.

        Contributor ranges allow the editor to explain where human reading effort accumulates.
        """
        self.function_name = function_name
        self.value = 0
        self.nesting = 0
        self.ranges: list[RangeJson] = []

    def add(self, amount: int, node: ast.AST) -> None:
        """Add cognitive points and record the contributing source range.

        The range is retained for future precise editor decorations.
        """
        self.value += amount
        self.ranges.append(source_range(node))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop at nested synchronous functions.

        Nested definitions receive independent cognitive scores.
        """
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Stop at nested asynchronous functions.

        Nested definitions receive independent cognitive scores.
        """
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Stop at nested classes.

        Class bodies are separate maintainability scopes.
        """
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Stop at lambda bodies.

        Lambda complexity is not attributed to the containing named function.
        """
        return

    def visit_If(self, node: ast.If) -> None:
        """Score if, elif, and else flow with nesting penalties.

        Elif chains remain at the same nesting level instead of being counted as nested if statements.
        """
        self.visit(node.test)
        self.add(1 + self.nesting, node)
        previous_nesting = self.nesting
        self.nesting += 1
        for statement in node.body:
            self.visit(statement)
        self.nesting = previous_nesting
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self.visit(node.orelse[0])
        elif node.orelse:
            self.add(1, node.orelse[0])
            for statement in node.orelse:
                self.visit(statement)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """Score a conditional expression without adding an else increment.

        Ternary expressions are compact but still interrupt linear reading flow.
        """
        self.add(1 + self.nesting, node)
        self.generic_visit(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        """Score a loop and visit its body at increased nesting.

        Loop else blocks remain at the surrounding nesting level.
        """
        self.add(1 + self.nesting, node)
        previous_nesting = self.nesting
        self.nesting += 1
        for statement in node.body:
            self.visit(statement)
        self.nesting = previous_nesting
        for statement in node.orelse:
            self.visit(statement)

    def visit_For(self, node: ast.For) -> None:
        """Score a synchronous loop.

        The shared loop implementation keeps for and while semantics consistent.
        """
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Score an asynchronous loop.

        Async loops contribute the same human control-flow cost as regular loops.
        """
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        """Score a while loop.

        While loops add one structural increment plus their nesting penalty.
        """
        self.visit(node.test)
        self._visit_loop(node)

    def visit_Try(self, node: ast.Try) -> None:
        """Score exception handlers while ignoring try and finally wrappers.

        Catch branches interrupt reading flow; try and finally are structural wrappers.
        """
        for statement in node.body:
            self.visit(statement)
        for handler in node.handlers:
            if handler.type is not None:
                self.visit(handler.type)
            self.add(1 + self.nesting, handler)
            previous_nesting = self.nesting
            self.nesting += 1
            for statement in handler.body:
                self.visit(statement)
            self.nesting = previous_nesting
        for statement in node.orelse + node.finalbody:
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        """Visit a with block without adding cognitive points.

        Context-manager syntax is considered readable shorthand rather than a control-flow break.
        """
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Visit an async with block without adding cognitive points.

        Async context-manager syntax follows the same readability rule as with.
        """
        self.visit_With(node)

    def visit_Match(self, node: ast.Match) -> None:
        """Score one match structure and visit case bodies at deeper nesting.

        Individual cases do not add points because the match structure is a single readable dispatch.
        """
        self.visit(node.subject)
        self.add(1 + self.nesting, node)
        previous_nesting = self.nesting
        self.nesting += 1
        for case in node.cases:
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
        self.nesting = previous_nesting

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Score one logical-operator sequence.

        Nested BoolOp nodes represent separate and/or sequences and are scored independently.
        """
        self.add(1, node)
        self.generic_visit(node)

    def visit_Break(self, node: ast.Break) -> None:
        """Score a break that interrupts linear loop reading.

        Early loop exits increase the effort needed to follow control flow.
        """
        self.add(1, node)

    def visit_Continue(self, node: ast.Continue) -> None:
        """Score a continue that interrupts linear loop reading.

        Loop jumps create another mental path through the function.
        """
        self.add(1, node)

    def visit_Call(self, node: ast.Call) -> None:
        """Score direct recursive calls and visit call arguments.

        Recursion requires the reader to track an additional execution context.
        """
        if isinstance(node.func, ast.Name) and node.func.id == self.function_name:
            self.add(1, node)
        self.generic_visit(node)


def cognitive_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, list[RangeJson]]:
    """Calculate cognitive complexity and its contributing ranges.

    The isolated visitor keeps nested definitions out of the parent function's score.
    """
    visitor = CognitiveVisitor(node.name)
    for statement in node.body:
        visitor.visit(statement)
    return visitor.value, visitor.ranges


class NestingVisitor(ast.NodeVisitor):
    """Find the deepest actual control-flow block in one function."""

    def __init__(self) -> None:
        """Initialize nesting depth and deepest ranges.

        Multiple blocks can tie for the deepest level and are retained together.
        """
        self.depth = 0
        self.maximum = 0
        self.ranges: list[RangeJson] = []

    def record_body(self, body: list[ast.stmt], depth: int) -> None:
        """Record a block and visit its statements at the requested depth.

        The block range is the editor target for nesting-specific highlighting.
        """
        current_range = block_range(body)
        if current_range is not None:
            if depth > self.maximum:
                self.maximum = depth
                self.ranges = [current_range]
            elif depth == self.maximum:
                self.ranges.append(current_range)
        previous_depth = self.depth
        self.depth = depth
        for statement in body:
            self.visit(statement)
        self.depth = previous_depth

    def visit_If(self, node: ast.If) -> None:
        """Visit if, elif, and else bodies without false nesting inflation.

        An elif chain is one conditional structure at the same branch depth.
        """
        self.record_body(node.body, self.depth + 1)
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self.visit(node.orelse[0])
        elif node.orelse:
            self.record_body(node.orelse, self.depth + 1)

    def visit_For(self, node: ast.For) -> None:
        """Visit a for loop body and optional else body.

        Each branch body is one block deeper than the surrounding scope.
        """
        self.record_body(node.body, self.depth + 1)
        if node.orelse:
            self.record_body(node.orelse, self.depth + 1)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Visit an async for loop body and optional else body.

        Async iteration follows the same nesting rules as for.
        """
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:
        """Visit a while loop body and optional else body.

        The loop body is one nested block below the containing scope.
        """
        self.record_body(node.body, self.depth + 1)
        if node.orelse:
            self.record_body(node.orelse, self.depth + 1)

    def visit_Try(self, node: ast.Try) -> None:
        """Visit try, except, else, and finally bodies as sibling blocks.

        Exception arms do not become nested inside one another.
        """
        self.record_body(node.body, self.depth + 1)
        for handler in node.handlers:
            self.record_body(handler.body, self.depth + 1)
        if node.orelse:
            self.record_body(node.orelse, self.depth + 1)
        if node.finalbody:
            self.record_body(node.finalbody, self.depth + 1)

    def visit_With(self, node: ast.With) -> None:
        """Visit a with body as one nested block.

        Multiple context items in one with statement still share one block.
        """
        self.record_body(node.body, self.depth + 1)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Visit an async with body as one nested block.

        Async context management follows the same nesting rule as with.
        """
        self.visit_With(node)

    def visit_Match(self, node: ast.Match) -> None:
        """Visit each match case body as one nested block.

        Cases are sibling branches beneath the match statement.
        """
        for case in node.cases:
            self.record_body(case.body, self.depth + 1)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Stop at nested synchronous functions.

        Nested function blocks are measured independently.
        """
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Stop at nested asynchronous functions.

        Nested async function blocks are measured independently.
        """
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Stop at nested classes.

        Class bodies are separate scopes.
        """
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Stop at lambda expressions.

        Lambda expressions do not contribute block nesting to named functions.
        """
        return


def nesting_depth(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, list[RangeJson]]:
    """Calculate maximum block nesting and deepest block ranges.

    Scope-aware traversal prevents nested functions and classes from inflating the result.
    """
    visitor = NestingVisitor()
    visitor.record_body(node.body, 0)
    return visitor.maximum, visitor.ranges


def significant_lines(source: str) -> set[int]:
    """Return one-based physical lines containing significant Python tokens.

    Token filtering excludes comments, blank lines, and continuation markers while preserving code lines.
    """
    ignored = {tokenize.ENCODING, tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.ENDMARKER}
    return {
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in ignored and token.string.strip()
    }


def scope_line_ranges(node: ast.AST) -> set[int]:
    """Return lines occupied by nested function and class scopes.

    Excluding nested scopes prevents the same source lines from inflating both parent and child metrics.
    """
    lines: set[int] = set()
    for child in ast.walk(node):
        if child is node or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = getattr(child, "lineno", 1)
        end = getattr(child, "end_lineno", start)
        lines.update(range(start, end + 1))
    return lines


def docstring_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Return lines occupied by the function's leading docstring.

    Only a string expression in the first body position is a Python docstring.
    """
    if not node.body or not isinstance(node.body[0], ast.Expr) or not isinstance(node.body[0].value, ast.Constant) or not isinstance(node.body[0].value.value, str):
        return set()
    first = node.body[0]
    start = getattr(first, "lineno", 1)
    end = getattr(first, "end_lineno", start)
    return set(range(start, end + 1))


def function_length(node: ast.FunctionDef | ast.AsyncFunctionDef, source_lines: set[int]) -> int:
    """Count executable source lines in a function definition.

    Blank lines, comments, leading docstrings, and nested scope bodies are excluded from the size metric.
    """
    start = node.lineno
    end = getattr(node, "end_lineno", start)
    excluded = docstring_lines(node) | scope_line_ranges(node)
    return len({line for line in source_lines if start <= line <= end and line not in excluded})


def parameter_details(spec: FunctionSpec) -> tuple[int, list[str]]:
    """Count explicit parameters and describe variadic declarations.

    The threshold follows common linter semantics while the details preserve explicit *args and **kwargs information.
    """
    args = spec.node.args
    positional = len(args.posonlyargs) + len(args.args)
    receiver = bool(spec.is_method and not spec.is_staticmethod and args.args and args.args[0].arg in {"self", "cls"})
    explicit = positional + len(args.kwonlyargs) - int(receiver)
    details: list[str] = []
    if args.vararg is not None:
        details.append("*args")
    if args.kwarg is not None:
        details.append("**kwargs")
    return explicit, details


def metric(
    name: MetricName,
    value: int,
    threshold: int,
    ranges: list[RangeJson],
    details: list[str] | None = None,
) -> MetricJson:
    """Build a typed function metric object.

    Centralizing metric serialization keeps thresholds, ranges, and details consistent.
    """
    return {"name": name, "value": value, "threshold": threshold, "ranges": ranges, "details": details or []}


def function_metrics(spec: FunctionSpec, source_lines: set[int], thresholds: Thresholds) -> list[MetricJson]:
    """Calculate all supported metrics for one function.

    Each metric receives its own value and source ranges while sharing the same scope boundary.
    """
    node = spec.node
    cognitive, cognitive_ranges = cognitive_complexity(node)
    nesting, nesting_ranges = nesting_depth(node)
    parameters, parameter_info = parameter_details(spec)
    full_range = [source_range(node)]
    return [
        metric("cyclomatic", cyclomatic_complexity(node), thresholds.cyclomatic, full_range),
        metric("cognitive", cognitive, thresholds.cognitive, cognitive_ranges or full_range),
        metric("nesting", nesting, thresholds.nesting, nesting_ranges or full_range),
        metric("functionLength", function_length(node, source_lines), thresholds.function_length, full_range),
        metric("parameters", parameters, thresholds.parameters, full_range, parameter_info),
    ]


def function_result(spec: FunctionSpec, source_lines: set[int], file_name: str, thresholds: Thresholds) -> FunctionJson:
    """Serialize one analyzed function with a stable source identity.

    The identity combines qualified name and location so duplicate local names remain navigable.
    """
    node = spec.node
    line = node.lineno - 1
    column = node.col_offset
    return {
        "id": f"{file_name}:{line}:{column}",
        "name": node.name,
        "qualifiedName": spec.qualified_name,
        "line": line,
        "column": column,
        "metrics": function_metrics(spec, source_lines, thresholds),
    }


def summary_for(name: MetricName, functions: list[FunctionJson], threshold: int) -> SummaryJson:
    """Aggregate maximum, average, and violations for one metric.

    Aggregates make the file-level dashboard explicit instead of overloading a single raw value.
    """
    values = [(function, next(metric for metric in function["metrics"] if metric["name"] == name)) for function in functions]
    if not values:
        return {"name": name, "maximum": 0, "average": 0.0, "threshold": threshold, "violatingFunctions": 0, "maximumFunctionId": None, "ranges": []}
    maximum_function, maximum_metric = max(values, key=lambda item: item[1]["value"])
    violating_functions = sum(metric_data["value"] > threshold for _, metric_data in values)
    return {
        "name": name,
        "maximum": maximum_metric["value"],
        "average": round(sum(metric_data["value"] for _, metric_data in values) / len(values), 1),
        "threshold": threshold,
        "violatingFunctions": violating_functions,
        "maximumFunctionId": maximum_function["id"],
        "ranges": maximum_metric["ranges"],
    }


def analyze(request: dict[str, object]) -> AnalysisJson:
    """Parse source and return structured file and function metrics.

    Syntax errors are returned as user-facing analysis errors instead of crashing the extension host.
    """
    raw_thresholds = cast(dict[str, object], request["thresholds"])
    thresholds = Thresholds(
        cyclomatic=int(cast(int, raw_thresholds["cyclomatic"])),
        cognitive=int(cast(int, raw_thresholds["cognitive"])),
        nesting=int(cast(int, raw_thresholds["nesting"])),
        function_length=int(cast(int, raw_thresholds["functionLength"])),
        parameters=int(cast(int, raw_thresholds["parameters"])),
    )
    source = str(request["source"])
    file_name = str(request["file"])
    try:
        tree = ast.parse(source, filename=file_name, type_comments=True)
        source_lines = significant_lines(source)
    except (SyntaxError, tokenize.TokenError) as error:
        line = getattr(error, "lineno", None) or getattr(error, "args", [None])[0]
        message = getattr(error, "msg", str(error))
        return {"file": file_name, "metrics": [], "functions": [], "error": f"Syntax error on line {line}: {message}"}
    specs = collect_functions(tree)
    function_results = [function_result(spec, source_lines, file_name, thresholds) for spec in specs]
    summaries = [
        summary_for("cyclomatic", function_results, thresholds.cyclomatic),
        summary_for("cognitive", function_results, thresholds.cognitive),
        summary_for("nesting", function_results, thresholds.nesting),
        summary_for("functionLength", function_results, thresholds.function_length),
        summary_for("parameters", function_results, thresholds.parameters),
    ]
    return {"file": file_name, "metrics": summaries, "functions": function_results, "error": None}


if __name__ == "__main__":
    print(json.dumps(analyze(cast(dict[str, object], json.load(sys.stdin)))))
