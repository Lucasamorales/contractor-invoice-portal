"""app/domain must stay pure (CLAUDE.md §5).

Zero imports from app.db, app.services, or any I/O library. This is the rule
that keeps the correctness layer testable without a database, so it is asserted
rather than trusted.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "app" / "domain"
# Each module's domain package is held to the same rule (modules/*/domain).
DOMAINS = [DOMAIN, *sorted((ROOT / "modules").glob("*/domain"))]

FORBIDDEN_PREFIXES = (
    "app.db",
    "app.services",
    "app.web",
    "app.config",
    "sqlalchemy",
    "alembic",
    "fastapi",
    "httpx",
    "requests",
    "openpyxl",
    "google",
    "weasyprint",
    "psycopg",
    "socket",
    "pathlib",
    "os",
    "io",
    "subprocess",
    "urllib",
)


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_domain_has_no_io_imports() -> None:
    offences: list[str] = []
    for directory in DOMAINS:
        for path in sorted(directory.glob("*.py")):
            for module in imported_modules(path.read_text(encoding="utf-8")):
                root = module.split(".")[0]
                if module.startswith(FORBIDDEN_PREFIXES) or root in FORBIDDEN_PREFIXES:
                    offences.append(
                        f"{directory.parent.name}/{path.name} imports {module}"
                    )
    assert offences == [], "a domain package must stay pure: " + "; ".join(offences)


def test_domain_is_not_empty() -> None:
    # Guards against the test above passing because it found nothing to check.
    assert len(list(DOMAIN.glob("*.py"))) >= 6
