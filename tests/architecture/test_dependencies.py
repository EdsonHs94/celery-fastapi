"""Reglas de dependencia para todas las importaciones de las capas internas."""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("layer", "allowed_layers"),
    [("domain", {"domain"}), ("application", {"domain", "application"})],
)
def test_inner_layers_only_import_standard_library_and_allowed_layers(
    layer, allowed_layers
):
    violations = []
    for path in sorted((ROOT / "app" / layer).rglob("*.py")):
        relative = path.relative_to(ROOT)
        package = ".".join(relative.with_suffix("").parts[:-1])
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = importlib.util.resolve_name(
                        "." * node.level + module, package
                    )
                modules = [module]
                # También contempla «from app import infrastructure» y relativos.
                if module == "app":
                    modules = [f"app.{alias.name}" for alias in node.names]
            for module in modules:
                parts = module.split(".")
                allowed = parts[0] in sys.stdlib_module_names or (
                    len(parts) > 1 and parts[0] == "app" and parts[1] in allowed_layers
                )
                if not allowed:
                    violations.append(f"{relative}:{node.lineno}: {module}")
    assert not violations, "Dependencias prohibidas:\n" + "\n".join(violations)
