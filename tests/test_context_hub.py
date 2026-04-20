from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.prompts.drafts import DRAFT_SYSTEM_PROMPT
from app.prompts.editorial import EDITORIAL_SYSTEM_PROMPT
from app.schemas.mvp_handoff import MvpHandoffPack, MvpPromptBundle
from app.services.context_hub import build_dynamic_context, get_static_context


def test_static_context_contains_velveteen_identity() -> None:
    context = get_static_context()
    assert "The Velveteen Project" in context
    assert "Applied Decision Systems Lab" in context
    assert "LinkedIn output rules" in context


def test_prompts_include_shared_velveteen_context() -> None:
    assert "The Velveteen Project" in EDITORIAL_SYSTEM_PROMPT
    assert "The Velveteen Project" in DRAFT_SYSTEM_PROMPT
    assert "LinkedIn output rules" in EDITORIAL_SYSTEM_PROMPT


async def test_dynamic_context_includes_recent_entities(db) -> None:
    with (
        patch(
            "app.services.context_hub.get_recent_signals",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_hub.get_recent_editorial_plans",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context_hub.get_recent_editorial_drafts",
            new=AsyncMock(return_value=[]),
        ),
    ):
        snapshot = await build_dynamic_context(db)

    assert "Dynamic working context" in snapshot
    assert "Priority repos:" in snapshot


@pytest.mark.asyncio
async def test_mvp_handoff_route_returns_200(client) -> None:
    pack = MvpHandoffPack(
        plan_id=8,
        signal_ids=[1, 2],
        thesis="A small signal-to-build workflow for climate risk research",
        scope_summary="Build a narrow workflow with tests and scope control.",
        context_basis=["shared_velveteen_context", "dynamic_db_snapshot"],
        prompt_architect=MvpPromptBundle(
            system_prompt="Architect system prompt long enough",
            user_prompt="Architect user prompt long enough to pass validation.",
        ),
        builder=MvpPromptBundle(
            system_prompt="Builder system prompt long enough",
            user_prompt="Builder user prompt long enough to pass validation.",
        ),
        auditor=MvpPromptBundle(
            system_prompt="Auditor system prompt long enough",
            user_prompt="Auditor user prompt long enough to pass validation.",
        ),
    )

    with patch(
        "app.api.routes.editorial.mvp_handoff.create_mvp_handoff_pack",
        new=AsyncMock(return_value=pack),
    ):
        response = await client.get("/api/v1/editorial/plans/8/mvp-handoff")

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == 8
    assert body["builder_target"] == "codex-or-antigravity"
