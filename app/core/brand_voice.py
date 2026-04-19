from pydantic import BaseModel


class BrandVoice(BaseModel):
    """
    System-level brand identity policy for The Velveteen Project.

    Treated as a core configuration object — not a prompt template.
    Injected into generation services (Phase 6) as structured context.
    """

    name: str = "The Velveteen Project"
    descriptor: str = "founder-led applied decision systems lab"

    tone_markers: list[str] = [
        "sobrio",
        "técnico",
        "claro",
        "humano",
        "reflexivo",
        "exigente con el rigor",
        "elegante pero no pretencioso",
        "orientado a insight antes que a autopromoción",
        "orientado a construcción antes que a espectáculo",
    ]

    anti_patterns: list[str] = [
        "hype exagerado",
        "frases tipo 'revolucionando la industria'",
        "engagement bait",
        "tono de influencer tecnológico",
        "dramatización artificial",
        "clichés de startup",
        "exceso de épica",
        "lenguaje corporativo hinchado",
        "marketing vacío",
        "comentarismo automático de noticias",
    ]

    editorial_rule: str = (
        "El sistema ayuda a pensar y articular. "
        "No convierte señales en contenido de forma automática. "
        "Cada output debe reflejar criterio real."
    )

    priority_domains: list[str] = [
        "machine learning",
        "NLP",
        "agentic workflows",
        "mathematical modeling",
        "applied research",
        "reliable systems",
        "software útil",
        "investigación aplicada",
        "LATAM",
        "educación",
        "clima y sostenibilidad",
        "salud",
        "riesgo y decisiones bajo incertidumbre",
    ]

    output_criteria: list[str] = [
        "novedad",
        "utilidad",
        "autenticidad",
        "evidencia",
        "alineación con la marca",
        "esfuerzo proporcional al valor",
        "coherencia de portafolio",
    ]


BRAND_VOICE = BrandVoice()
