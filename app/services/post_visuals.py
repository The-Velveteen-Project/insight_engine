"""
Visuals for a LinkedIn post (Phase 3.7): a recommendation and, when the
post cites a number, a sober data card rendered deterministically.

No model call. The recommendation is rule-based on what the post mentions;
the card is matplotlib with the Velveteen palette and the font matplotlib
bundles, so every card belongs to the same family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

import aiosqlite

from app.db.queries import get_signals_by_ids
from app.schemas.linkedin import LinkedInPostRecord
from app.services.editorial_planner import get_persisted_editorial_plan
from app.utils.text import trim_to_boundary

_BG = "#0f1412"
_INK = "#e8e6df"
_TEAL = "#3aa6a0"
_MUTED = "#7a8480"
_REST = "#2a3330"
_SITE = "thevelveteenproject.vercel.app"

_PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:[.,]\d+)?)\s?%")
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?![\w%])")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_OWN_WORK: dict[str, str] = {
    "carmen": "una foto del póster de CARMEN en AIiH o una captura del dashboard",
    "cemracs": (
        "la figura del capacity factor contra el caudal (CEMRACS) "
        "o la foto del pizarrón"
    ),
    "ecoagent": "una captura del mapa de alertas de EcoAgent/ALLO",
    "allo": "una captura del mapa de alertas de EcoAgent/ALLO",
    "stochastogreen": "una captura del dashboard de StochastoGreen con un ticker real",
    "antigenlm": "una figura del embedding de HA/NA con la deriva temporal coloreada",
    "antigensde": (
        "una trayectoria simulada del latent SDE (una sola figura, sin decoración)"
    ),
    "insight engine": (
        "una captura del chat de Telegram con un plan aprobado (sin datos personales)"
    ),
    "velveteen": (
        "una captura del chat de Telegram con un plan aprobado (sin datos personales)"
    ),
}


@dataclass(frozen=True)
class NumberClaim:
    value_text: str
    percent: float | None
    sentence: str


@dataclass
class VisualPlan:
    recommendations: list[str] = field(default_factory=list)
    claim: NumberClaim | None = None
    card_png: bytes | None = None
    source_title: str | None = None


def _sentences(text: str) -> list[str]:
    cleaned = " ".join(text.split())
    return [s.strip() for s in _SENTENCE_RE.split(cleaned) if s.strip()]


def extract_number_claim(body: str) -> NumberClaim | None:
    """First sentence carrying a percentage, else the first with a real number."""
    for sentence in _sentences(body):
        match = _PERCENT_RE.search(sentence)
        if match:
            raw = match.group(1).replace(",", ".")
            try:
                percent = float(raw)
            except ValueError:
                continue
            if 0 < percent <= 100:
                return NumberClaim(f"{match.group(1)}%", percent, sentence)
    for sentence in _sentences(body):
        if sentence.startswith("#"):
            continue
        match = _NUMBER_RE.search(sentence)
        if match and len(match.group(1).replace(",", "").replace(".", "")) >= 2:
            return NumberClaim(match.group(1), None, sentence)
    return None


def recommend(record: LinkedInPostRecord, claim: NumberClaim | None) -> list[str]:
    lowered = record.body.lower()
    picks: list[str] = []
    for key, advice in _OWN_WORK.items():
        if key in lowered and advice not in picks:
            picks.append(f"Tu propio material: {advice}.")
    if claim is not None:
        picks.append(
            f"Tarjeta de datos con el número que ya citas ({claim.value_text}); "
            "te la adjunto abajo."
        )
    if any(
        word in lowered for word in ("presented", "poster", "conference", "presenté")
    ):
        picks.append("Una foto real del evento vale más que cualquier gráfico.")
    if not picks:
        picks.append(
            "Este post no cita un número ni un artefacto tuyo. Publícalo sin imagen: "
            "un texto bien escrito no pierde alcance, y una ilustración genérica "
            "sí resta."
        )
    return picks[:3]


def render_data_card(
    *,
    title: str,
    claim: NumberClaim,
    caption: str,
    source: str | None,
) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10.8, 5.4), dpi=200)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.axis("off")
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.9, 0.9)

    ax.text(
        0,
        0.62,
        trim_to_boundary(title, 90),
        color=_INK,
        fontsize=15,
        fontweight="bold",
        va="center",
    )
    if claim.percent is not None:
        value = claim.percent
        ax.barh([0], [value], color=_TEAL, height=0.42)
        ax.barh([0], [100 - value], left=[value], color=_REST, height=0.42)
        ax.text(
            value / 2,
            0,
            claim.value_text,
            color=_BG,
            fontsize=20,
            fontweight="bold",
            ha="center",
            va="center",
        )
        if value <= 85:
            ax.text(
                value + (100 - value) / 2,
                0,
                f"{100 - value:.1f}%".replace(".0%", "%"),
                color=_INK,
                fontsize=12,
                ha="center",
                va="center",
            )
    else:
        ax.text(
            0,
            0.05,
            claim.value_text,
            color=_TEAL,
            fontsize=44,
            fontweight="bold",
            va="center",
        )
    ax.text(
        0,
        -0.5,
        _wrap(caption, 78),
        color=_INK,
        fontsize=11.5,
        va="top",
    )
    footer = f"Source: {source} · {_SITE}" if source else _SITE
    ax.text(
        100,
        0.84,
        trim_to_boundary(footer, 110),
        color=_MUTED,
        fontsize=8.5,
        ha="right",
        va="center",
    )
    plt.tight_layout(pad=1.2)
    buffer = BytesIO()
    plt.savefig(buffer, format="png", facecolor=_BG)
    plt.close(fig)
    return buffer.getvalue()


def _wrap(text: str, width: int) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:3])


async def _source_title(
    db: aiosqlite.Connection, record: LinkedInPostRecord
) -> str | None:
    if record.plan_id is None:
        return None
    try:
        plan = await get_persisted_editorial_plan(db, record.plan_id)
    except LookupError:
        return None
    rows = await get_signals_by_ids(db, plan.proposal.signal_ids)
    for row in rows:
        title = str(row["title"] or "").strip()
        if title:
            return trim_to_boundary(title, 70)
    return None


async def plan_visual(
    db: aiosqlite.Connection, record: LinkedInPostRecord
) -> VisualPlan:
    claim = extract_number_claim(record.body)
    plan = VisualPlan(recommendations=recommend(record, claim), claim=claim)
    if claim is None:
        return plan
    plan.source_title = await _source_title(db, record)
    title = trim_to_boundary(record.hook or record.body, 90)
    plan.card_png = render_data_card(
        title=title,
        claim=claim,
        caption=trim_to_boundary(claim.sentence, 230),
        source=plan.source_title,
    )
    return plan
