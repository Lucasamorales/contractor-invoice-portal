"""The walls between the invoice app and the modules (docs/decisions/0001).

The hub idea only works if a module cannot quietly reach into another, so the
rule is a failing test rather than a sentence in a document. These run on the
source with no database and no network.

Today there is one module (``modules/leave``) and the invoice app is still in
``app/``. The rules are written for where it is going, so the invoice code can
move later without these changing:

* ``app`` never imports ``modules``: the invoice app does not depend on leave.
* ``modules`` never import ``app``: leave does not depend on invoicing.
* no module imports another module.
* a module's ``domain/`` package uses the standard library and nothing else, and
  never reads the clock. A calculation somebody will check months later has to
  give the same answer when it is asked again.

Not covered yet, and why, is in modules/leave/BACKLOG.md (H-01 .. H-09).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
MODULES = ROOT / "modules"

# Reading the wall clock makes a calculation depend on the day it is run, which
# is how a test that passed yesterday fails today. Dates are parameters.
CLOCK_CALLS = {"today", "now", "utcnow"}
CLOCK_IMPORTS = {"time"}


def python_files(directory: Path) -> Iterator[Path]:
    yield from sorted(
        p for p in directory.rglob("*.py") if "__pycache__" not in p.parts
    )


def imports_of(path: Path) -> set[str]:
    """Every absolute module name a file imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def module_names() -> list[str]:
    return sorted(
        p.name for p in MODULES.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


def domain_dirs() -> list[Path]:
    return [
        MODULES / name / "domain"
        for name in module_names()
        if (MODULES / name / "domain").is_dir()
    ]


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_there_is_something_to_check() -> None:
    """Guards against every test below passing because it found nothing."""
    assert "leave" in module_names()
    assert len(list(python_files(MODULES / "leave" / "domain"))) >= 6
    assert len(list(python_files(APP))) > 30


def test_the_invoice_app_does_not_import_a_module() -> None:
    offences = [
        f"{relative(path)} imports {name}"
        for path in python_files(APP)
        for name in imports_of(path)
        if name == "modules" or name.startswith("modules.")
    ]
    assert offences == [], "app/ must not depend on modules/: " + "; ".join(offences)


def test_a_module_does_not_import_the_invoice_app() -> None:
    offences = [
        f"{relative(path)} imports {name}"
        for path in python_files(MODULES)
        for name in imports_of(path)
        if name == "app" or name.startswith("app.")
    ]
    assert offences == [], "modules/ must not depend on app/: " + "; ".join(offences)


@pytest.mark.parametrize("name", module_names())
def test_a_module_does_not_import_another_module(name: str) -> None:
    others = [m for m in module_names() if m != name]
    offences = [
        f"{relative(path)} imports {imported}"
        for path in python_files(MODULES / name)
        for imported in imports_of(path)
        for other in others
        if imported == f"modules.{other}" or imported.startswith(f"modules.{other}.")
    ]
    assert offences == [], f"modules/{name} reached into another module: " + "; ".join(
        offences
    )


@pytest.mark.parametrize("domain", domain_dirs(), ids=lambda d: d.parent.name)
def test_a_module_domain_uses_the_standard_library_and_nothing_else(
    domain: Path,
) -> None:
    """Stronger than a list of forbidden libraries: anything that is not the
    standard library, or the domain package itself, fails. A new dependency in a
    calculation has to be a conscious decision, and CLAUDE.md §8 says to ask."""
    own = f"modules.{domain.parent.name}.domain"
    offences = []
    for path in python_files(domain):
        for name in imports_of(path):
            root = name.split(".")[0]
            if (
                root in sys.stdlib_module_names
                or name == own
                or name.startswith(own + ".")
            ):
                continue
            offences.append(f"{relative(path)} imports {name}")
    assert offences == [], "a domain package must be pure: " + "; ".join(offences)


@pytest.mark.parametrize("domain", domain_dirs(), ids=lambda d: d.parent.name)
def test_a_module_domain_never_reads_the_clock(domain: Path) -> None:
    offences = []
    for path in python_files(domain):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CLOCK_CALLS
            ):
                offences.append(
                    f"{relative(path)}:{node.lineno} calls .{node.func.attr}()"
                )
        offences.extend(
            f"{relative(path)} imports {name}"
            for name in imports_of(path)
            if name.split(".")[0] in CLOCK_IMPORTS
        )
    assert offences == [], "pass the date in as a parameter: " + "; ".join(offences)
