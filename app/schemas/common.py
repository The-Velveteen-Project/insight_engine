from enum import StrEnum


class MessageType(StrEnum):
    """
    Structural type of the incoming Telegram event.

    Classification priority (Phase 2): voice > url > reply > text
    - voice:    message contains a voice note
    - url:      text whose primary content is a URL to be fetched/analyzed
    - reply:    reply to a previous message (no URL, not voice)
    - text:     plain text
    - document: file or attachment
    - unknown:  unrecognized payload
    """

    TEXT = "text"
    VOICE = "voice"
    URL = "url"
    REPLY = "reply"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class EditorialChannel(StrEnum):
    """
    Editorial lane that describes the narrative nature of the content.
    Distinct from MessageType — a voice note can belong to any channel.
    Assigned by classifier in Phase 2 (deterministic rules, no LLM).
    """

    SIGNAL = "signal"  # external news, papers, articles, trends
    BUILD = "build"  # project updates, architecture, code decisions
    RESEARCH = "research"  # thesis, papers, mathematical ideas, hypotheses
    TEACHING = "teaching"  # courses, classes, pedagogy, formative learning
    PHILOSOPHY = "philosophy"  # reflections on AI, tools, product, modeling


class OutputType(StrEnum):
    """Possible outputs the engine can produce for a given signal or message."""

    LINKEDIN_DRAFT = "linkedin_draft"
    TECHNICAL_NOTE = "technical_note"
    MVP_IDEA = "mvp_idea"
    MVP_SPEC = "mvp_spec"
    PORTFOLIO_UPDATE = "portfolio_update"
    README_UPDATE = "readme_update"
    WEBSITE_UPDATE = "website_update"
    INTERNAL_NOTE = "internal_note"
    DISCARD = "discard"


class ProcessingStatus(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    PROCESSED = "processed"
    APPROVED = "approved"
    DISCARDED = "discarded"


class SignalSource(StrEnum):
    """Origin type of an external signal (Phase 4)."""

    NEWS = "news"
    PAPER = "paper"
    ARTICLE = "article"
    GITHUB = "github"
    USER_SUBMITTED = "user_submitted"
    INTERNAL = "internal"
