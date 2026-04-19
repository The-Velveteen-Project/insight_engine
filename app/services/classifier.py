import re
from dataclasses import dataclass

from app.schemas.common import EditorialChannel, MessageType
from app.schemas.telegram import TelegramMessage

# Strips trailing punctuation that often attaches to URLs in natural text.
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)
_URL_TRAIL = re.compile(r"[.,;:!?)]+$")

# Keyword lists are intentionally specific to minimize false positives.
# `channel` is a provisional heuristic — the LLM in Phase 6 will refine it
# with full semantic context. A None result is preferable to a wrong label.
_CHANNEL_KEYWORDS: dict[str, list[str]] = {
    EditorialChannel.SIGNAL: [
        "arxiv",
        "paper",
        "article",
        "news",
        "new model",
        "report",
        "study",
        "announced",
        "openai",
        "deepmind",
        "anthropic",
        "gpt",
        "llm",
        "benchmark",
        "dataset",
        "preprint",
        "published",
        "researchers",
        "scientists",
        "journal",
    ],
    EditorialChannel.BUILD: [
        "commit",
        "deploy",
        "refactor",
        "pull request",
        "branch",
        "feature",
        "bug fix",
        "migration",
        "docker",
        "kubernetes",
        "shipped",
        "changelog",
        "endpoint",
        "service",
        "database",
        "api",
        "release v",
        "merged",
        "ci/cd",
    ],
    EditorialChannel.RESEARCH: [
        "thesis",
        "tesis",
        "hypothesis",
        "hipótesis",
        "experiment",
        "methodology",
        "training loss",
        "evaluation",
        "ablation",
        "gradient",
        "convergence",
        "baseline",
        "metric",
        "proof",
        "theorem",
        "lemma",
        "derivation",
        "model architecture",
    ],
    EditorialChannel.TEACHING: [
        "clase",
        "class",
        "course",
        "lecture",
        "exercise",
        "homework",
        "assignment",
        "student",
        "estudiante",
        "profesor",
        "teacher",
        "tutorial",
        "workshop",
        "taller",
        "enseñar",
        "aprender",
        "quiz",
    ],
    EditorialChannel.PHILOSOPHY: [
        "i think",
        "creo que",
        "i believe",
        "reflect",
        "reflexión",
        "interesante",
        "perspective",
        "tradeoff",
        "principle",
        "debate",
        "critique",
        "opinion",
        "thoughts on",
        "what if",
        "wonder",
    ],
}


@dataclass
class MessageClassification:
    message_type: str
    has_url: bool
    is_reply: bool
    source_url: str | None
    reply_to_telegram_id: int | None
    channel: str | None


def extract_url(text: str) -> str | None:
    """
    Returns the first HTTP/HTTPS URL found in `text`, or None.

    `source_url` in the domain model represents the primary (first) URL only.
    Multiple URLs in a single message are not tracked in Phase 3.
    """
    m = _URL_RE.search(text)
    if not m:
        return None
    return _URL_TRAIL.sub("", m.group(0))


def classify_channel(text_lower: str) -> str | None:
    """
    Assigns a provisional editorial channel based on keyword scoring.

    Returns the highest-scoring channel, or None if no keywords match.
    Exposed publicly so post-transcription re-classification can use it.
    """
    scores: dict[str, int] = {}
    for channel, keywords in _CHANNEL_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > 0:
            scores[channel] = count
    if not scores:
        return None
    return max(scores, key=lambda c: scores[c])


def classify(msg: TelegramMessage) -> MessageClassification:
    text = msg.text or msg.caption or ""

    url = extract_url(text) if text else None
    has_url = url is not None
    is_reply = msg.reply_to_message is not None
    reply_to_id = msg.reply_to_message.message_id if msg.reply_to_message else None

    # Priority: voice > url > reply > document > text
    if msg.voice:
        message_type = MessageType.VOICE
    elif has_url:
        message_type = MessageType.URL
    elif is_reply:
        message_type = MessageType.REPLY
    elif msg.document:
        message_type = MessageType.DOCUMENT
    else:
        message_type = MessageType.TEXT

    channel = classify_channel(text.lower()) if text else None

    return MessageClassification(
        message_type=message_type,
        has_url=has_url,
        is_reply=is_reply,
        source_url=url,
        reply_to_telegram_id=reply_to_id,
        channel=channel,
    )
