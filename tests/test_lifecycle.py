"""Verify static lifecycle graph construction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from lifecycle import LifecycleRequest, lifecycle  # noqa: E402


class LifecycleTests(unittest.TestCase):
    """Exercise exact workspace call resolution.

    Small in-memory modules isolate graph behavior from filesystem and editor state.
    """

    def test_resolves_transitive_import_alias_and_method_calls(self) -> None:
        """Resolve callers and callees across supported static forms.

        This covers the primary workspace lifecycle path without dynamic inference.
        """
        root = "/workspace"
        source_a = """from service import Service as ImportedService
from worker import run as execute

def entry():
    execute()

def orchestrate():
    entry()

def imported_method():
    ImportedService.handle()
"""
        source_worker = """def run():
    return external_call()
"""
        source_service = """class Service:
    @staticmethod
    def handle():
        return helper()

def helper():
    return 1
"""
        request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/app.py", "workspaceRoot": root, "module": "app", "source": source_a},
                {"path": f"{root}/worker.py", "workspaceRoot": root, "module": "worker", "source": source_worker},
                {"path": f"{root}/service.py", "workspaceRoot": root, "module": "service", "source": source_service},
            ],
            "selectedSymbolId": f"{root}/app.py:3:0",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        self.assertIsNone(result["error"])
        labels = {node["label"]: node["role"] for node in result["nodes"]}
        self.assertEqual(labels["entry"], "selected")
        self.assertEqual(labels["orchestrate"], "entrypoint")
        self.assertEqual(labels["run"], "callee")
        self.assertEqual(labels["external_call"], "unresolved")

    def test_resolves_nested_functions_cycles_and_self_methods(self) -> None:
        """Resolve lexical children, recursive cycles, and exact self calls.

        These relationships must remain distinct without broad name matching.
        """
        root = "/workspace"
        source = """def outer():
    def inner():
        outer()
    inner()

class Service:
    def first(self):
        self.second()

    def second(self):
        self.first()
"""
        nested_request: LifecycleRequest = {
            "files": [{"path": f"{root}/module.py", "workspaceRoot": root, "module": "module", "source": source}],
            "selectedSymbolId": f"{root}/module.py:0:0",
            "maxNodes": 100,
        }
        method_request: LifecycleRequest = {
            **nested_request,
            "selectedSymbolId": f"{root}/module.py:6:4",
        }

        nested_result = lifecycle(nested_request)
        method_result = lifecycle(method_request)

        nested_labels = {node["label"]: node["role"] for node in nested_result["nodes"]}
        self.assertEqual(nested_labels["outer.<locals>.inner"], "both")
        method_labels = {node["label"]: node["role"] for node in method_result["nodes"]}
        self.assertEqual(method_labels["Service.second"], "both")

    def test_resolves_relative_imports(self) -> None:
        """Resolve package-relative imported functions.

        Relative imports are common application boundaries and must remain workspace-root scoped.
        """
        root = "/workspace"
        request: LifecycleRequest = {
            "files": [
                {
                    "path": f"{root}/package/app.py",
                    "workspaceRoot": root,
                    "module": "package.app",
                    "source": "from .worker import run\n\ndef entry():\n    run()\n",
                },
                {
                    "path": f"{root}/package/worker.py",
                    "workspaceRoot": root,
                    "module": "package.worker",
                    "source": "def run():\n    return 1\n",
                },
            ],
            "selectedSymbolId": f"{root}/package/app.py:2:0",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        labels = {node["label"]: node["role"] for node in result["nodes"]}
        self.assertEqual(labels["run"], "callee")

    def test_labels_main_guard_and_decorated_upstream_entrypoints(self) -> None:
        """Label exact startup boundaries before the selected function.

        Entrypoint evidence makes the upstream graph begin at application dispatch instead of an arbitrary caller.
        """
        root = "/workspace"
        source = """def target():
    return 1

def service():
    target()

def main():
    service()

if __name__ == "__main__":
    main()
"""
        route_source = """from app import target

@api.get("/items")
def route():
    target()
"""
        main_request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/app.py", "workspaceRoot": root, "module": "app", "source": source},
            ],
            "selectedSymbolId": f"{root}/app.py:0:0",
            "maxNodes": 100,
        }
        route_request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/app.py", "workspaceRoot": root, "module": "app", "source": source},
                {"path": f"{root}/routes.py", "workspaceRoot": root, "module": "routes", "source": route_source},
            ],
            "selectedSymbolId": f"{root}/app.py:0:0",
            "maxNodes": 100,
        }

        main_result = lifecycle(main_request)
        route_result = lifecycle(route_request)

        main_nodes = {node["label"]: node for node in main_result["nodes"]}
        self.assertEqual(main_nodes["main"]["role"], "entrypoint")
        self.assertEqual(main_nodes["main"]["entrypointReason"], "__main__ guard entrypoint")
        self.assertFalse(main_nodes["main"]["isTest"])
        route_nodes = {node["label"]: node for node in route_result["nodes"]}
        self.assertEqual(route_nodes["route"]["role"], "entrypoint")
        self.assertEqual(route_nodes["route"]["entrypointReason"], "@api.get entrypoint")

    def test_resolves_constructed_and_injected_instance_methods(self) -> None:
        """Resolve exact receiver types from constructors, annotations, and initialized attributes.

        Instance dispatch is the common bridge between repository entrypoints and selected business functions.
        """
        root = "/workspace"
        source = """class Worker:
    def run(self):
        target()

def target():
    return 1

class Service:
    def __init__(self):
        self.worker = Worker()

    def execute(self):
        self.worker.run()

def injected(worker: Worker):
    worker.run()

def main():
    service = Service()
    service.execute()

if __name__ == "__main__":
    main()
"""
        request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/app.py", "workspaceRoot": root, "module": "app", "source": source},
            ],
            "selectedSymbolId": f"{root}/app.py:4:0",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        nodes = {node["label"]: node for node in result["nodes"]}
        self.assertEqual(nodes["main"]["role"], "entrypoint")
        self.assertIn("Service.execute", nodes)
        self.assertIn("Worker.run", nodes)
        self.assertIn("injected", nodes)

    def test_resolves_literal_getattr_protocol_dispatch(self) -> None:
        """Resolve optional protocol methods dispatched through a literal getattr alias.

        Structural compatibility connects service wrappers to every provable workspace implementation.
        """
        root = "/workspace"
        source = """class Embedder:
    def embed_text(self, text):
        raise NotImplementedError

def embed_many(embedder: Embedder, texts):
    batch = getattr(embedder, "embed_texts", None)
    if callable(batch):
        return batch(texts)
    return [embedder.embed_text(text) for text in texts]

class Provider:
    def embed_text(self, text):
        return [1.0]

    def embed_texts(self, texts):
        return [[1.0] for text in texts]

def entry(provider: Provider):
    return embed_many(provider, ["text"])
"""
        request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/embedding.py", "workspaceRoot": root, "module": "embedding", "source": source},
            ],
            "selectedSymbolId": f"{root}/embedding.py:14:4",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        nodes = {node["label"]: node for node in result["nodes"]}
        self.assertIn("embed_many", nodes)
        self.assertIn("entry", nodes)

    def test_keeps_duplicate_names_separate_and_reports_invalid_files(self) -> None:
        """Keep same-named functions distinct across modules and skip invalid syntax.

        Stable source IDs and warnings prevent collisions or whole-workspace failure.
        """
        root = "/workspace"
        request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/a.py", "workspaceRoot": root, "module": "a", "source": "def duplicate():\n    return 1\n"},
                {"path": f"{root}/b.py", "workspaceRoot": root, "module": "b", "source": "def duplicate():\n    return 2\n"},
                {"path": f"{root}/broken.py", "workspaceRoot": root, "module": "broken", "source": "def broken(:\n"},
            ],
            "selectedSymbolId": f"{root}/a.py:0:0",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        resolved_duplicates = [
            node for node in result["nodes"]
            if node["label"] == "duplicate" and node["role"] != "unresolved"
        ]
        self.assertEqual(len(resolved_duplicates), 1)
        self.assertEqual(len(result["warnings"]), 1)

    def test_truncates_large_reachable_graph(self) -> None:
        """Stop traversal at the configured node bound.

        An explicit truncation flag keeps partial lifecycle results transparent.
        """
        root = "/workspace"
        source = """def first():
    second()

def second():
    third()

def third():
    return 1
"""
        request: LifecycleRequest = {
            "files": [{"path": f"{root}/chain.py", "workspaceRoot": root, "module": "chain", "source": source}],
            "selectedSymbolId": f"{root}/chain.py:0:0",
            "maxNodes": 2,
        }

        result = lifecycle(request)

        resolved_nodes = [node for node in result["nodes"] if node["role"] != "unresolved"]
        self.assertEqual(len(resolved_nodes), 2)
        self.assertTrue(result["truncated"])

    def test_renders_class_ownership_construction_and_inheritance(self) -> None:
        """Render exact structural relationships around a selected class.

        Class pivots must explain how a class is created, extended, and implemented.
        """
        root = "/workspace"
        source = """class Base:
    def run(self):
        return 1

class Service(Base):
    def __init__(self):
        pass

    def execute(self):
        self.run()

def build():
    return Service()
"""
        request: LifecycleRequest = {
            "files": [
                {"path": f"{root}/service.py", "workspaceRoot": root, "module": "service", "source": source},
            ],
            "selectedSymbolId": f"{root}/service.py:4:0",
            "maxNodes": 100,
        }

        result = lifecycle(request)

        nodes_by_id = {node["id"]: node for node in result["nodes"]}
        labels = {node["label"]: node for node in result["nodes"]}
        relationships = {
            (
                nodes_by_id[edge["source"]]["label"],
                nodes_by_id[edge["target"]]["label"],
                edge["kind"],
            )
            for edge in result["edges"]
        }
        self.assertEqual(labels["Service"]["kind"], "class")
        self.assertIn(("Base", "Service", "inherits"), relationships)
        self.assertIn(("Service", "Service.execute", "contains"), relationships)
        self.assertIn(("build", "Service", "constructs"), relationships)


if __name__ == "__main__":
    unittest.main()
