"""Build a workspace-wide static Python function call graph."""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from typing import Literal, TypedDict, cast


class SourceFileRequest(TypedDict):
    """Describe one workspace source file.

    Carrying the module name from VS Code keeps workspace-root resolution explicit.
    """

    path: str
    workspaceRoot: str
    module: str
    source: str


class LifecycleRequest(TypedDict):
    """Describe a lifecycle analysis request.

    A bounded request prevents an unexpectedly large graph from overwhelming the webview.
    """

    files: list[SourceFileRequest]
    selectedFunctionId: str
    maxNodes: int


class LifecycleNodeJson(TypedDict):
    """Represent one graph node.

    Exact source identities make every resolved node safely navigable.
    """

    id: str
    label: str
    detail: str
    uri: str | None
    line: int | None
    column: int | None
    role: Literal["selected", "entrypoint", "caller", "callee", "both", "unresolved"]
    entrypointReason: str | None
    isTest: bool


class LifecycleEdgeJson(TypedDict):
    """Represent one directed call edge.

    Directed edges preserve the real caller-to-callee relationship.
    """

    source: str
    target: str


class LifecycleResultJson(TypedDict):
    """Represent the lifecycle analyzer response.

    The response includes partial-result metadata instead of silently dropping information.
    """

    selectedFunctionId: str
    nodes: list[LifecycleNodeJson]
    edges: list[LifecycleEdgeJson]
    truncated: bool
    warnings: list[str]
    error: str | None


@dataclass(frozen=True)
class ImportTarget:
    """Store an exact imported symbol target.

    Separating modules from symbols avoids guessing whether an attribute is callable.
    """

    canonical: str
    is_module: bool


@dataclass(frozen=True)
class FunctionDefinition:
    """Store one discovered function and its lexical context.

    The context supports exact resolution of local functions and class methods.
    """

    id: str
    name: str
    qualified_name: str
    canonical: str
    module: str
    path: str
    line: int
    column: int
    class_canonical: str | None
    parent_function_canonical: str | None
    node: ast.FunctionDef | ast.AsyncFunctionDef
    imports: dict[str, ImportTarget]


@dataclass(frozen=True)
class ModuleIndex:
    """Store definitions and imports belonging to one module.

    Module-local indexes keep resolution deterministic across duplicate names.
    """

    name: str
    path: str
    tree: ast.Module
    definitions: tuple[FunctionDefinition, ...]
    imports: dict[str, ImportTarget]


class DefinitionCollector(ast.NodeVisitor):
    """Collect functions with scope-aware canonical names.

    Tracking lexical scopes prevents duplicate and nested names from being conflated.
    """

    def __init__(self, path: str, module: str, imports: dict[str, ImportTarget]) -> None:
        """Initialize collection state.

        Explicit state makes scope transitions reversible while walking the AST.
        """
        self.path = path
        self.module = module
        self.module_imports = imports
        self.definitions: list[FunctionDefinition] = []
        self.scope: list[tuple[str, Literal["class", "function"]]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit definitions inside a class.

        Class scope is retained so self, cls, and explicit class calls resolve exactly.
        """
        self.scope.append((node.name, "class"))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Collect a synchronous function and its nested definitions.

        One shared implementation keeps synchronous and asynchronous identities identical.
        """
        self._collect_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Collect an asynchronous function and its nested definitions.

        Async functions participate in the same static call graph as regular functions.
        """
        self._collect_function(node)

    def _collect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Create one function definition and traverse its nested scopes.

        Canonical names are stable across files and remain distinct for nested functions.
        """
        parts = [name for name, _ in self.scope]
        qualified_parts: list[str] = []
        for name, kind in self.scope:
            if kind == "function":
                qualified_parts.extend((name, "<locals>"))
            else:
                qualified_parts.append(name)
        qualified_name = ".".join((*qualified_parts, node.name))
        canonical = f"{self.module}.{qualified_name}" if self.module else qualified_name
        class_names = [name for name, kind in self.scope if kind == "class"]
        function_names = [name for name, kind in self.scope if kind == "function"]
        class_canonical = f"{self.module}.{'.'.join(class_names)}" if class_names else None
        parent_parts: list[str] = []
        for name, kind in self.scope:
            if kind == "function":
                parent_parts.extend((name, "<locals>"))
            elif function_names:
                parent_parts.append(name)
        parent_function = (
            f"{self.module}.{'.'.join(parent_parts)}"
            if function_names and self.module
            else ".".join(parent_parts) or None
        )
        definition = FunctionDefinition(
            id=f"{self.path}:{node.lineno - 1}:{node.col_offset}",
            name=node.name,
            qualified_name=qualified_name,
            canonical=canonical,
            module=self.module,
            path=self.path,
            line=node.lineno - 1,
            column=node.col_offset,
            class_canonical=class_canonical,
            parent_function_canonical=parent_function,
            node=node,
            imports=self.module_imports,
        )
        self.definitions.append(definition)
        self.scope.append((node.name, "function"))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()


class BodyCallCollector(ast.NodeVisitor):
    """Collect syntactic calls owned by one execution body.

    Nested definitions are skipped so their calls are not attributed to the parent.
    """

    def __init__(self, body: list[ast.stmt]) -> None:
        """Initialize the collector for one statement body.

        Accepting statements supports both function bodies and module entry guards.
        """
        self.body = body
        self.calls: list[ast.Call] = []

    def collect(self) -> list[ast.Call]:
        """Collect calls from the root function body.

        Traversing statements individually avoids treating the root as a nested definition.
        """
        for statement in self.body:
            self.visit(statement)
        return self.calls

    def visit_Call(self, node: ast.Call) -> None:
        """Record a call and inspect its arguments.

        Calls inside arguments belong to the same executing function.
        """
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Skip a nested synchronous definition.

        Its body is analyzed under its own function identity.
        """

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Skip a nested asynchronous definition.

        Its body is analyzed under its own function identity.
        """

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Skip lambda bodies.

        Lambdas have no stable function row identity in the current extension.
        """


class BindingCollector(ast.NodeVisitor):
    """Collect exact variable-to-class bindings from one function body.

    Constructor assignments and annotations resolve common instance method calls without type guessing.
    """

    def __init__(
        self,
        owner: FunctionDefinition,
        class_canonicals: set[str],
    ) -> None:
        """Initialize binding collection for one function.

        Known workspace classes bound the inference to definitions the analyzer can prove.
        """
        self.owner = owner
        self.class_canonicals = class_canonicals
        self.bindings: dict[str, str] = {}

    def collect(self) -> dict[str, str]:
        """Collect parameter and body bindings.

        A single result map feeds call resolution for the owning function only.
        """
        arguments = (
            *self.owner.node.args.posonlyargs,
            *self.owner.node.args.args,
            *self.owner.node.args.kwonlyargs,
        )
        for argument in arguments:
            if argument.annotation:
                target = self._resolve_class(argument.annotation)
                if target:
                    self.bindings[argument.arg] = target
        for statement in self.owner.node.body:
            self.visit(statement)
        return self.bindings

    def visit_Assign(self, node: ast.Assign) -> None:
        """Collect simple constructor assignments.

        Multi-target assignments retain the same proven constructed class for every exact target.
        """
        target_class = self._class_from_value(node.value)
        if target_class:
            for target in node.targets:
                self._bind(target, target_class)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Collect annotated assignments and constructor values.

        An exact constructor takes precedence while a known annotation covers dependency-injected values.
        """
        target_class = self._class_from_value(node.value) if node.value else None
        target_class = target_class or self._resolve_class(node.annotation)
        if target_class:
            self._bind(node.target, target_class)
        if node.value:
            self.generic_visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Skip nested synchronous definitions.

        Their local bindings belong to a separate function context.
        """

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Skip nested asynchronous definitions.

        Their local bindings belong to a separate function context.
        """

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Skip lambda bodies.

        Lambda-local bindings cannot affect the owning function's exact receiver types.
        """

    def _class_from_value(self, value: ast.expr) -> str | None:
        """Resolve a constructor expression to a workspace class.

        Only direct calls to exact class names create a binding.
        """
        return self._resolve_class(value.func) if isinstance(value, ast.Call) else None

    def _resolve_class(self, expression: ast.expr) -> str | None:
        """Resolve an annotation or constructor name to a workspace class.

        Imported and module-local class names use the same exact binding rules as calls.
        """
        parts = attribute_parts(expression)
        if not parts:
            return None
        candidates: list[str] = []
        imported = self.owner.imports.get(parts[0])
        if imported:
            candidates.append(".".join((imported.canonical, *parts[1:])))
        candidates.append(f"{self.owner.module}.{'.'.join(parts)}")
        return next((candidate for candidate in candidates if candidate in self.class_canonicals), None)

    def _bind(self, target: ast.expr, class_canonical: str) -> None:
        """Bind an exact name or attribute target to a class.

        Computed assignment targets are ignored because they do not provide stable receiver identities.
        """
        parts = attribute_parts(target)
        if parts:
            self.bindings[".".join(parts)] = class_canonical


class GetattrAliasCollector(ast.NodeVisitor):
    """Collect local callable aliases created by exact getattr expressions.

    This captures optional protocol dispatch while preserving the literal requested method name.
    """

    def __init__(self) -> None:
        """Initialize callable alias collection.

        Alias targets are resolved later against proven receiver bindings and workspace classes.
        """
        self.aliases: list[tuple[str, ast.expr, str]] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        """Collect a simple name assigned from getattr.

        Exact receiver and literal method arguments avoid interpreting arbitrary reflection.
        """
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "getattr"
            and len(node.value.args) >= 2
            and isinstance(node.value.args[1], ast.Constant)
            and isinstance(node.value.args[1].value, str)
        ):
            self.aliases.append((
                node.targets[0].id,
                node.value.args[0],
                node.value.args[1].value,
            ))
        self.generic_visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Skip nested synchronous definitions.

        Their aliases belong to a separate function execution context.
        """

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Skip nested asynchronous definitions.

        Their aliases belong to a separate function execution context.
        """

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Skip lambda bodies.

        Lambda-local aliases cannot affect calls owned by the enclosing function.
        """


def callable_alias_targets(
    owner: FunctionDefinition,
    bindings: dict[str, str],
    methods_by_class: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    """Resolve exact getattr callable aliases to compatible workspace methods.

    Structural compatibility follows the annotated receiver contract and retains every valid implementation.
    """
    collector = GetattrAliasCollector()
    for statement in owner.node.body:
        collector.visit(statement)
    targets: dict[str, set[str]] = {}
    for alias, receiver_expression, method_name in collector.aliases:
        receiver_parts = attribute_parts(receiver_expression)
        receiver_class = bindings.get(".".join(receiver_parts)) if receiver_parts else None
        if not receiver_class:
            continue
        receiver_methods = methods_by_class.get(receiver_class, {})
        direct_target = receiver_methods.get(method_name)
        if direct_target:
            targets[alias] = {direct_target}
            continue
        required_methods = set(receiver_methods)
        compatible_targets = {
            methods[method_name]
            for methods in methods_by_class.values()
            if required_methods.issubset(methods) and method_name in methods
        }
        if compatible_targets:
            targets[alias] = compatible_targets
    return targets


def collect_imports(
    tree: ast.Module,
    workspace_root: str,
    module: str,
    is_package: bool,
) -> dict[str, ImportTarget]:
    """Collect exact module-level import bindings.

    Only explicit Python bindings are used so aliases are resolved without fuzzy matching.
    """
    imports: dict[str, ImportTarget] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".")[0]
                canonical = alias.name if alias.asname else alias.name.split(".")[0]
                imports[local_name] = ImportTarget(f"{workspace_root}|{canonical}", True)
        elif isinstance(statement, ast.ImportFrom):
            package_parts = module.split(".") if is_package else module.split(".")[:-1]
            if statement.level > 0:
                retained = max(0, len(package_parts) - statement.level + 1)
                base_parts = package_parts[:retained]
            else:
                base_parts = []
            imported_module = ".".join((*base_parts, *(statement.module or "").split("."))).strip(".")
            if not imported_module:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                imports[local_name] = ImportTarget(
                    f"{workspace_root}|{imported_module}.{alias.name}",
                    False,
                )
    return imports


def attribute_parts(node: ast.expr) -> list[str] | None:
    """Return the exact dotted parts of a name or attribute expression.

    Rejecting computed receivers prevents dynamic calls from being guessed.
    """
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        prefix = attribute_parts(node.value)
        return [*prefix, node.attr] if prefix else None
    return None


def resolve_call(
    call: ast.Call,
    owner: FunctionDefinition,
    definitions: dict[str, FunctionDefinition],
    bindings: dict[str, str],
) -> tuple[str | None, str]:
    """Resolve one call to a known canonical function when exact.

    Unresolved labels are returned for transparent graph leaves instead of guessed edges.
    """
    parts = attribute_parts(call.func)
    if not parts:
        return None, "<dynamic call>"
    label = ".".join(parts)
    candidates: list[str] = []
    first = parts[0]
    imported = owner.imports.get(first)
    if imported:
        suffix = parts[1:]
        candidates.append(".".join((imported.canonical, *suffix)))
    if len(parts) == 1:
        candidates.append(f"{owner.canonical}.<locals>.{first}")
        if owner.parent_function_canonical:
            candidates.append(f"{owner.parent_function_canonical}.<locals>.{first}")
        candidates.append(f"{owner.module}.{first}" if owner.module else first)
    if len(parts) > 1 and first in {"self", "cls"} and owner.class_canonical:
        candidates.append(".".join((owner.class_canonical, *parts[1:])))
    for prefix_length in range(len(parts) - 1, 0, -1):
        receiver = ".".join(parts[:prefix_length])
        bound_class = bindings.get(receiver)
        if bound_class:
            candidates.append(".".join((bound_class, *parts[prefix_length:])))
            break
    if len(parts) > 1:
        candidates.append(f"{owner.module}.{label}" if owner.module else label)
    for candidate in candidates:
        if candidate in definitions:
            return candidate, label
    return None, label


def resolve_module_call(
    call: ast.Call,
    module: ModuleIndex,
    definitions: dict[str, FunctionDefinition],
) -> str | None:
    """Resolve one call executed directly by a module entry guard.

    Module-level resolution uses only exact local definitions and explicit imports.
    """
    parts = attribute_parts(call.func)
    if not parts:
        return None
    first = parts[0]
    candidates: list[str] = []
    imported = module.imports.get(first)
    if imported:
        candidates.append(".".join((imported.canonical, *parts[1:])))
    if len(parts) == 1:
        candidates.append(f"{module.name}.{first}")
    elif not imported:
        candidates.append(f"{module.name}.{'.'.join(parts)}")
    return next((candidate for candidate in candidates if candidate in definitions), None)


def is_main_guard(statement: ast.stmt) -> bool:
    """Return whether a statement is an exact Python main guard.

    Exact AST matching avoids classifying arbitrary name comparisons as application startup.
    """
    if not isinstance(statement, ast.If):
        return False
    comparison = statement.test
    if not isinstance(comparison, ast.Compare) or len(comparison.ops) != 1 or len(comparison.comparators) != 1:
        return False
    if not isinstance(comparison.ops[0], ast.Eq):
        return False
    left = comparison.left
    right = comparison.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def decorated_entrypoint_reason(definition: FunctionDefinition) -> str | None:
    """Return the reason a decorated function is an application entrypoint.

    A small exact decorator catalog covers common web and command dispatch boundaries.
    """
    entrypoint_decorators = {
        "command",
        "delete",
        "get",
        "listener",
        "patch",
        "post",
        "put",
        "route",
        "websocket",
    }
    for decorator in definition.node.decorator_list:
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        parts = attribute_parts(expression)
        if parts and parts[-1] in entrypoint_decorators:
            return f"@{'.'.join(parts)} entrypoint"
    return None


def is_test_definition(definition: FunctionDefinition) -> bool:
    """Return whether a function belongs to test code.

    Exact module and filename conventions support a UI filter without hiding application symbols heuristically.
    """
    module = definition.module.split("|", 1)[-1]
    parts = module.split(".")
    filename = definition.path.rsplit("/", 1)[-1]
    return "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py")


def module_index(source_file: SourceFileRequest) -> tuple[ModuleIndex | None, str | None]:
    """Parse and index one workspace module.

    Syntax errors are isolated so other valid workspace files can still contribute.
    """
    try:
        tree = ast.parse(source_file["source"], filename=source_file["path"], type_comments=True)
    except SyntaxError as error:
        return None, f"{source_file['path']}: syntax error on line {error.lineno}: {error.msg}"
    imports = collect_imports(
        tree,
        source_file["workspaceRoot"],
        source_file["module"],
        source_file["path"].endswith("/__init__.py"),
    )
    internal_module = f"{source_file['workspaceRoot']}|{source_file['module']}"
    collector = DefinitionCollector(source_file["path"], internal_module, imports)
    collector.visit(tree)
    return ModuleIndex(
        internal_module,
        source_file["path"],
        tree,
        tuple(collector.definitions),
        imports,
    ), None


def lifecycle(request: LifecycleRequest) -> LifecycleResultJson:
    """Build the reachable lifecycle graph for the selected function.

    Bidirectional traversal exposes both entry paths and downstream effects with a hard size bound.
    """
    indexes: list[ModuleIndex] = []
    warnings: list[str] = []
    for source_file in request["files"]:
        index, warning = module_index(source_file)
        if index:
            indexes.append(index)
        if warning:
            warnings.append(warning)
    by_canonical = {
        definition.canonical: definition
        for index in indexes
        for definition in index.definitions
    }
    by_id = {definition.id: definition for definition in by_canonical.values()}
    selected = by_id.get(request["selectedFunctionId"])
    if not selected:
        return {
            "selectedFunctionId": request["selectedFunctionId"],
            "nodes": [],
            "edges": [],
            "truncated": False,
            "warnings": warnings,
            "error": "The selected function was not found in the workspace index.",
        }

    resolved_edges: set[tuple[str, str]] = set()
    unresolved_by_owner: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {canonical: set() for canonical in by_canonical}
    incoming: dict[str, set[str]] = {canonical: set() for canonical in by_canonical}
    class_canonicals = {
        definition.class_canonical
        for definition in by_canonical.values()
        if definition.class_canonical
    }
    bindings_by_owner = {
        owner.canonical: BindingCollector(owner, class_canonicals).collect()
        for owner in by_canonical.values()
    }
    class_attribute_bindings: dict[str, str] = {}
    for owner in by_canonical.values():
        for receiver, target_class in bindings_by_owner[owner.canonical].items():
            if receiver.startswith("self.") and owner.class_canonical:
                class_attribute_bindings[f"{owner.class_canonical}.{receiver.removeprefix('self.')}"] = target_class
    methods_by_class: dict[str, dict[str, str]] = {}
    for definition in by_canonical.values():
        if definition.class_canonical:
            methods_by_class.setdefault(definition.class_canonical, {})[
                definition.name
            ] = definition.canonical
    for owner in by_canonical.values():
        bindings = dict(bindings_by_owner[owner.canonical])
        if owner.class_canonical:
            prefix = f"{owner.class_canonical}."
            bindings.update({
                f"self.{receiver.removeprefix(prefix)}": target_class
                for receiver, target_class in class_attribute_bindings.items()
                if receiver.startswith(prefix)
            })
        callable_aliases = callable_alias_targets(owner, bindings, methods_by_class)
        for call in BodyCallCollector(owner.node.body).collect():
            if isinstance(call.func, ast.Name) and call.func.id in callable_aliases:
                for target in callable_aliases[call.func.id]:
                    resolved_edges.add((owner.canonical, target))
                    outgoing[owner.canonical].add(target)
                    incoming[target].add(owner.canonical)
                continue
            target, label = resolve_call(call, owner, by_canonical, bindings)
            if target:
                resolved_edges.add((owner.canonical, target))
                outgoing[owner.canonical].add(target)
                incoming[target].add(owner.canonical)
            else:
                unresolved_by_owner.setdefault(owner.canonical, set()).add(label)

    entrypoint_reasons: dict[str, str] = {}
    for definition in by_canonical.values():
        decorator_reason = decorated_entrypoint_reason(definition)
        if decorator_reason:
            entrypoint_reasons[definition.canonical] = decorator_reason
        elif definition.name == "main":
            entrypoint_reasons[definition.canonical] = "Conventional main() entrypoint"
    for index in indexes:
        for statement in index.tree.body:
            if not is_main_guard(statement):
                continue
            guard = cast(ast.If, statement)
            for call in BodyCallCollector(guard.body).collect():
                target = resolve_module_call(call, index, by_canonical)
                if target:
                    entrypoint_reasons[target] = "__main__ guard entrypoint"
    for canonical in by_canonical:
        if not incoming[canonical]:
            entrypoint_reasons.setdefault(canonical, "No resolved callers")

    max_nodes = max(1, min(request["maxNodes"], 100))
    selected_canonical = selected.canonical
    caller_nodes: set[str] = set()
    callee_nodes: set[str] = set()
    truncated = False

    def traverse(adjacency: dict[str, set[str]], reached: set[str]) -> None:
        """Traverse one graph direction from the selection.

        The shared node bound applies across both directions for predictable rendering cost.
        """
        nonlocal truncated
        pending = [selected_canonical]
        visited = {selected_canonical}
        while pending:
            current = pending.pop(0)
            for neighbor in sorted(adjacency[current]):
                if neighbor in visited:
                    continue
                if len(caller_nodes | callee_nodes | {selected_canonical, neighbor}) > max_nodes:
                    truncated = True
                    continue
                visited.add(neighbor)
                reached.add(neighbor)
                pending.append(neighbor)

    traverse(incoming, caller_nodes)
    traverse(outgoing, callee_nodes)
    resolved = caller_nodes | callee_nodes | {selected_canonical}
    nodes: list[LifecycleNodeJson] = []
    for canonical in sorted(resolved):
        definition = by_canonical[canonical]
        if canonical == selected_canonical:
            role: Literal["selected", "entrypoint", "caller", "callee", "both", "unresolved"] = "selected"
        elif canonical in entrypoint_reasons:
            role = "entrypoint"
        elif canonical in caller_nodes and canonical in callee_nodes:
            role = "both"
        elif canonical in caller_nodes:
            role = "caller"
        else:
            role = "callee"
        nodes.append({
            "id": definition.id,
            "label": definition.qualified_name,
            "detail": (
                f"{entrypoint_reasons[canonical]} · "
                f"{definition.module.split('|', 1)[-1]} · L{definition.line + 1}"
                if canonical in entrypoint_reasons
                else f"{definition.module.split('|', 1)[-1]} · L{definition.line + 1}"
            ),
            "uri": definition.path,
            "line": definition.line,
            "column": definition.column,
            "role": role,
            "entrypointReason": entrypoint_reasons.get(canonical),
            "isTest": is_test_definition(definition),
        })
    canonical_to_id = {canonical: by_canonical[canonical].id for canonical in resolved}
    edges: list[LifecycleEdgeJson] = [
        {"source": canonical_to_id[source], "target": canonical_to_id[target]}
        for source, target in sorted(resolved_edges)
        if source in resolved and target in resolved
    ]
    unresolved_count = len(nodes)
    for owner in sorted(resolved):
        for label in sorted(unresolved_by_owner.get(owner, set())):
            if unresolved_count >= max_nodes:
                truncated = True
                break
            node_id = f"unresolved:{canonical_to_id[owner]}:{label}"
            nodes.append({
                "id": node_id,
                "label": label,
                "detail": "Unresolved or external call",
                "uri": None,
                "line": None,
                "column": None,
                "role": "unresolved",
                "entrypointReason": None,
                "isTest": is_test_definition(by_canonical[owner]),
            })
            edges.append({"source": canonical_to_id[owner], "target": node_id})
            unresolved_count += 1
    return {
        "selectedFunctionId": request["selectedFunctionId"],
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "warnings": warnings,
        "error": None,
    }


def main() -> None:
    """Read one request and write one lifecycle response.

    A single JSON exchange keeps the analyzer isolated from the extension host.
    """
    request = cast(LifecycleRequest, json.load(sys.stdin))
    print(json.dumps(lifecycle(request)))


if __name__ == "__main__":
    main()
