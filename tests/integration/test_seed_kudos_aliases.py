"""Seeding the kudos name mappings from a reviewed file (R-KUD-13).

Every mapping decides who gets paid for a kudo, so the script must show what it
would do, refuse to write while anything is unresolved, never overwrite a
mapping somebody already made, and attach the kudos already in the ledger to
the person a name is mapped to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import KudosAlias, KudosEvent
from app.scripts import seed_kudos_aliases as script
from app.scripts.seed_kudos_aliases import Row
from tests.integration.conftest import make_person

pytestmark = pytest.mark.integration

JANE = "jane@example.com"


def _event(session, key: str, receiver: str, **extra) -> None:
    session.add(
        KudosEvent(
            event_key=key,
            receiver_name=receiver,
            giver_name="Ben Branch",
            awarded_on=date(2026, 7, 1),
            **extra,
        )
    )


@pytest.fixture
def ledger(factory) -> None:
    """Jane exists, and the sheet has given kudos to two spellings of names."""
    with factory() as session, session.begin():
        make_person(session, email=JANE)
        _event(session, "k1", "Jane  SMITH")
        _event(session, "k2", "jane smith")
        _event(session, "k3", "Fred Stray")


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "aliases.csv"
    # A BOM, as a spreadsheet export carries one.
    path.write_text("﻿" + text, encoding="utf-8")
    return path


def test_the_file_is_read_with_emails_lower_cased_and_blank_names_skipped(
    tmp_path,
) -> None:
    path = _file(
        tmp_path,
        "sheet_name,email,note\n"
        " Jane Smith , Jane@Example.com ,\n"
        ",ignored@example.com,no name so not a row\n"
        "Fred Stray,,left the company\n",
    )
    assert script.read_file(path) == [
        Row(sheet_name="Jane Smith", email=JANE, note=""),
        Row(sheet_name="Fred Stray", email="", note="left the company"),
    ]


def test_the_plan_says_what_each_row_would_do_and_how_many_kudos_it_moves(
    factory, ledger
) -> None:
    rows = [Row("Jane Smith", JANE, ""), Row("Fred Stray", "", "left the company")]
    with factory() as session:
        actions, problems = script.plan(session, rows)
    assert problems == []
    assert actions == [
        f"  map     Jane Smith, holding 2 kudos -> {JANE}",
        "  ignore  Fred Stray, holding 1 kudos — left the company",
    ]


def test_the_plan_reports_an_unknown_email_and_a_name_listed_twice(
    factory, ledger
) -> None:
    rows = [
        Row("Jane Smith", JANE, ""),
        Row("JANE  smith", JANE, "same person, different spelling"),
        Row("Dan Wise", "dan@example.com", ""),
    ]
    with factory() as session:
        actions, problems = script.plan(session, rows)
    assert len(actions) == 1
    assert problems == [
        "JANE  smith: listed twice",
        "Dan Wise: no person with email dan@example.com",
    ]


def test_applying_maps_the_name_and_attaches_its_unclaimed_kudos(
    factory, ledger
) -> None:
    rows = [Row("Jane Smith", JANE, ""), Row("Fred Stray", "", "left the company")]
    with factory() as session, session.begin():
        assert script.apply(session, rows, actor="accounts@example.com") == 2

    with factory() as session:
        aliases = {a.normalised_name: a for a in session.scalars(select(KudosAlias))}
        jane = aliases["jane smith"]
        assert jane.person_id is not None
        assert jane.ignored is False
        assert jane.confirmed_by == "accounts@example.com"
        fred = aliases["fred stray"]
        assert (fred.person_id, fred.ignored, fred.note) == (
            None,
            True,
            "left the company",
        )

        owners = {e.event_key: e.person_id for e in session.scalars(select(KudosEvent))}
        assert owners["k1"] == owners["k2"] == jane.person_id
        assert owners["k3"] is None, "an ignored name's kudos belong to nobody"


def test_a_kudo_that_already_belongs_to_somebody_is_not_reassigned(factory) -> None:
    with factory() as session, session.begin():
        jane = make_person(session, email=JANE)
        other = make_person(session, email="ann@example.com")
        _event(session, "k1", "Jane Smith", person_id=other.id)
        script.apply(session, [Row("Jane Smith", JANE, "")], actor="seed")
        assert jane.id != other.id
        owner = session.scalar(
            select(KudosEvent.person_id).where(KudosEvent.event_key == "k1")
        )
        assert owner == other.id


def test_a_name_already_mapped_is_left_exactly_as_it_was(factory, ledger) -> None:
    with factory() as session, session.begin():
        script.apply(session, [Row("Jane Smith", "", "decided earlier")], actor="first")

    rows = [Row("Jane Smith", JANE, "")]
    with factory() as session, session.begin():
        actions, problems = script.plan(session, rows)
        assert (actions, problems) == (["  skip    Jane Smith (already mapped)"], [])
        assert script.apply(session, rows, actor="second") == 0

    with factory() as session:
        alias = session.scalar(select(KudosAlias))
        assert (alias.ignored, alias.confirmed_by) == (True, "first")


def _run(monkeypatch, factory, settings: Settings, *argv: str) -> int:
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "get_sessionmaker", lambda: factory)
    return script.main(list(argv))


STAGING = Settings(_env_file=None, environment="staging")
GOOD = f"sheet_name,email,note\nJane Smith,{JANE},\nFred Stray,,left the company\n"
BAD = "sheet_name,email,note\nDan Wise,dan@example.com,\n"


def _aliases(factory) -> int:
    with factory() as session:
        return len(session.scalars(select(KudosAlias)).all())


def test_a_dry_run_prints_the_plan_and_writes_nothing(
    monkeypatch, factory, ledger, tmp_path, capsys
) -> None:
    code = _run(monkeypatch, factory, STAGING, "--file", str(_file(tmp_path, GOOD)))
    out = capsys.readouterr().out
    assert code == 0
    assert "2 rows read from" in out
    assert "Dry run. Nothing written." in out
    assert _aliases(factory) == 0


def test_a_dry_run_with_problems_exits_non_zero(
    monkeypatch, factory, ledger, tmp_path, capsys
) -> None:
    code = _run(monkeypatch, factory, STAGING, "--file", str(_file(tmp_path, BAD)))
    assert code == 1
    assert "! Dan Wise: no person with email dan@example.com" in capsys.readouterr().out


def test_commit_writes_the_aliases(
    monkeypatch, factory, ledger, tmp_path, capsys
) -> None:
    path = str(_file(tmp_path, GOOD))
    assert _run(monkeypatch, factory, STAGING, "--file", path, "--commit") == 0
    assert "Wrote 2 aliases." in capsys.readouterr().out
    with factory() as session:
        confirmed = {a.confirmed_by for a in session.scalars(select(KudosAlias))}
    assert confirmed == {"seed"}


def test_commit_refuses_while_any_row_has_a_problem(
    monkeypatch, factory, ledger, tmp_path, capsys
) -> None:
    """One bad row stops the good ones too: a half-applied file is harder to audit."""
    path = str(_file(tmp_path, GOOD + "Dan Wise,dan@example.com,\n"))
    assert _run(monkeypatch, factory, STAGING, "--file", path, "--commit") == 2
    assert "Refusing to commit" in capsys.readouterr().err
    assert _aliases(factory) == 0


def test_a_missing_file_is_reported(monkeypatch, factory, tmp_path, capsys) -> None:
    missing = str(tmp_path / "nope.csv")
    assert _run(monkeypatch, factory, STAGING, "--file", missing) == 2
    assert "no such file" in capsys.readouterr().err


def test_production_needs_to_know_who_is_deciding(
    monkeypatch, factory, ledger, tmp_path, capsys
) -> None:
    production = Settings(_env_file=None, environment="production")
    path = str(_file(tmp_path, GOOD))
    assert _run(monkeypatch, factory, production, "--file", path, "--commit") == 2
    assert "--actor is required in production" in capsys.readouterr().err
    assert _aliases(factory) == 0

    code = _run(
        monkeypatch,
        factory,
        production,
        "--file",
        path,
        "--commit",
        "--actor",
        "accounts@example.com",
    )
    assert code == 0
    with factory() as session:
        confirmed = {a.confirmed_by for a in session.scalars(select(KudosAlias))}
    assert confirmed == {"accounts@example.com"}
