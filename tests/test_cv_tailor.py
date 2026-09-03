"""
Phase 2.6: master CV storage, gap analysis, tailored CV.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import insert_job_lead, set_job_lead_posting_text
from app.schemas.cv import CVEntry, GapAnalysis, RequirementEvidence, TailoredCV
from app.schemas.telegram import TelegramUpdate
from app.services import cv_tailor
from app.services.message_intake import handle_update
from app.services.telegram_orchestrator import handle_command, handle_operator_text
from tests.test_telegram_html import assert_valid_telegram_html

_MASTER = """# CV maestro

## Identity

Carlos Manuel Orrego Franco
Manizales, Colombia · cmorregofranco@gmail.com

## Experience

### Research Fellow — CEMRACS 2026 · CIRM · Jul – Aug 2026
- Three-parameter hydrology model beat a random forest and a neural net out of sample.
- [VERIFY] Something not yet confirmed.

## Skills

- Scientific ML: PyTorch, JAX, neural SDEs.
"""


def _gap() -> GapAnalysis:
    return GapAnalysis(
        verdict="apply_with_tailoring",
        verdict_reason="The forecasting result matches; agents experience is adjacent.",
        covered=[
            RequirementEvidence(
                requirement="time-series forecasting",
                evidence="Three-parameter hydrology model beat RF & NN",
                strength="strong",
            )
        ],
        missing=["5+ years in industry"],
        foreground=["CEMRACS hydrology model", "CARMEN forecasting engine"],
        keywords_to_mirror=["time-series forecasting"],
        opener=(
            "At CEMRACS my three-parameter model beat a neural net. "
            "This role is that problem at scale."
        ),
    )


def _cv() -> TailoredCV:
    return TailoredCV(
        headline="Research Engineer, Scientific ML · stochastic modeling · forecasting",
        summary=(
            "Applied mathematician building forecasting systems with structure "
            "written into the equations first. Recent result: a three-parameter "
            "hydrology model that beat a random forest and a neural network."
        ),
        highlighted_projects=[
            CVEntry(
                title="CEMRACS 2026 hydrology model",
                subtitle="CIRM, Marseille · 2026",
                bullets=["Three-parameter model beat RF and NN out of sample."],
            )
        ],
        experience=[
            CVEntry(
                title="Research Fellow", subtitle="CIRM", bullets=["Owned hydrology."]
            )
        ],
        education=["M.Sc. Applied Mathematics, UNAL, 2024–2026, GPA 5.0/5.0"],
        skills=["Scientific ML: PyTorch, JAX, neural SDEs"],
        tailoring_notes="Foregrounded CEMRACS; omitted teaching; cannot show 5+ years.",
    )


async def _lead_with_text(db: aiosqlite.Connection, tag: str) -> int:
    lead_id, _ = await insert_job_lead(
        db,
        source="exa",
        source_id=None,
        title="Research Engineer, Forecasting",
        company="Deepgram",
        url=f"https://jobs.lever.co/deepgram/{tag}",
        location=None,
        remote=True,
        summary="",
        published_at=None,
        fit_score=0.6,
        fit_note="rol: research engineer",
        dream=False,
    )
    await set_job_lead_posting_text(
        db, lead_id=lead_id, posting_text="We need time-series forecasting. 5+ years."
    )
    return lead_id


@pytest.fixture
def master_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "cv_master.md"
    path.write_text(_MASTER, encoding="utf-8")
    monkeypatch.setattr("app.services.cv_tailor.settings.cv_master_path", str(path))
    return path


async def test_load_master_from_file_drops_verify_lines(
    db: aiosqlite.Connection, master_file: Path
) -> None:
    await cv_tailor.set_operator_state(db, cv_tailor.CV_MASTER_KEY, "")
    master = await cv_tailor.load_master(db)
    assert master.source == "file"
    assert "[VERIFY]" not in master.text
    assert master.dropped_verify_lines == 1
    assert master.identity_block.startswith("Carlos Manuel Orrego Franco")


async def test_gap_command_persists_and_formats(
    db: aiosqlite.Connection, master_file: Path, monkeypatch
) -> None:
    lead_id = await _lead_with_text(db, "gap")

    class _Analyst:
        async def generate(self, *, system: str, user: str) -> GapAnalysis:
            assert "MASTER CV" in user and "[VERIFY]" not in user
            return _gap()

    monkeypatch.setattr("app.services.cv_tailor.get_gap_analyst", lambda: _Analyst())
    monkeypatch.setattr(
        "app.services.cv_tailor.get_job_details_extractor", lambda: None, raising=False
    )
    text = await handle_operator_text(
        f"analiza mi fit con la vacante {lead_id}", db, chat_id=6100
    )
    assert text is not None
    assert "aplica, con el CV reordenado" in text
    assert "5+ years" in text
    assert_valid_telegram_html(text)
    stored = await cv_tailor.stored_gap(db, lead_id)
    assert stored is not None and stored.verdict == "apply_with_tailoring"


async def test_cv_command_sends_document_and_uses_master_identity(
    db: aiosqlite.Connection, master_file: Path, monkeypatch
) -> None:
    lead_id = await _lead_with_text(db, "cv")

    class _Analyst:
        async def generate(self, *, system: str, user: str) -> GapAnalysis:
            return _gap()

    class _Writer:
        async def generate(self, *, system: str, user: str) -> TailoredCV:
            assert "GAP ANALYSIS ALREADY DONE" in user
            return _cv()

    monkeypatch.setattr("app.services.cv_tailor.get_gap_analyst", lambda: _Analyst())
    monkeypatch.setattr("app.services.cv_tailor.get_cv_writer", lambda: _Writer())
    sent = AsyncMock()
    with patch("app.services.telegram_orchestrator.send_document", sent):
        text = await handle_command(f"/cv {lead_id}", db, chat_id=6101)

    sent.assert_awaited_once()
    kwargs = sent.await_args.kwargs
    assert kwargs["filename"] == f"Carlos_Orrego_CV_deepgram_{lead_id}.md"
    body = kwargs["content"].decode("utf-8")
    assert body.startswith("Carlos Manuel Orrego Franco")
    assert "cmorregofranco@gmail.com" in body
    assert "## Selected projects" in body and "CEMRACS 2026 hydrology model" in body
    assert "va como archivo Markdown" in text
    assert_valid_telegram_html(text)


async def test_cv_without_master_is_honest(
    db: aiosqlite.Connection, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "app.services.cv_tailor.settings.cv_master_path", str(tmp_path / "missing.md")
    )
    await cv_tailor.set_operator_state(db, cv_tailor.CV_MASTER_KEY, "")
    text = await handle_command("/cv 1", db)
    assert "No tengo tu CV maestro" in text
    status = await handle_command("/cv_master", db)
    assert "No tengo tu CV maestro" in status


async def test_cv_master_upload_through_telegram_document(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    payload = {
        "update_id": 990001,
        "message": {
            "message_id": 4242,
            "from": {"id": 7, "is_bot": False, "first_name": "Carlos"},
            "chat": {"id": 6102, "type": "private"},
            "date": 1_700_000_000,
            "caption": "cv_master",
            "document": {
                "file_id": "doc-1",
                "file_unique_id": "u1",
                "file_name": "cv_master.md",
                "mime_type": "text/markdown",
            },
        },
    }
    update = TelegramUpdate.model_validate(payload)
    sent: list[str] = []

    async def _send(chat_id: int, text: str, parse_mode: str = "HTML") -> None:
        sent.append(text)

    monkeypatch.setattr("app.services.message_intake.send_message", _send)
    monkeypatch.setattr(
        "app.services.message_intake.download_file",
        AsyncMock(return_value=_MASTER.encode("utf-8")),
    )
    await handle_update(update, db, json.dumps(payload))

    assert sent and "CV maestro guardado" in sent[0]
    assert_valid_telegram_html(sent[0])
    master = await cv_tailor.load_master(db)
    assert master.source == "db"
    assert master.dropped_verify_lines == 1
