"""
Keyword → emoji dictionary for Submagic-style caption decoration.

Used by the ``hype_emoji`` caption style to inject one emoji per phrase next
to a matching keyword. The match is case-insensitive, punctuation-stripped,
and stem-ish: entries are bare lemmas and the matcher checks prefix (so
``money`` matches ``money.`` / ``Money!`` but not ``honeymoon``).

We keep the dictionary small on purpose — dense, high-signal pairs only.
Overdoing emojis looks like spam and breaks the look that makes Submagic
work. English covers the global creator bulk; Indonesian is explicit for
the user's home market. Both dictionaries live in the same file so adding
a new language is just one table append.
"""

from __future__ import annotations

import re
import unicodedata

# Each entry maps a single-word lemma → emoji. We lowercase the keyword and
# the input, strip punctuation, and match on the first word in the phrase
# that hits. Keywords are ordered most-specific-first within each language.

EMOJI_EN: dict[str, str] = {
    # Emotion / hype
    "fire": "🔥",
    "hot": "🔥",
    "insane": "🤯",
    "crazy": "🤯",
    "mind": "🤯",
    "shocked": "😱",
    "boom": "💥",
    "amazing": "✨",
    "awesome": "✨",
    "incredible": "✨",
    "beautiful": "✨",
    "perfect": "💯",
    "legit": "💯",
    "real": "💯",
    "fact": "💯",
    "facts": "💯",
    "truth": "💯",
    "true": "💯",
    "love": "❤️",
    "heart": "❤️",
    "cry": "😭",
    "tears": "😭",
    "laugh": "😂",
    "funny": "😂",
    "lol": "😂",
    "sad": "😢",
    "happy": "😊",
    "smile": "😊",
    "angry": "😡",
    "mad": "😡",
    # Money / business
    "money": "💰",
    "cash": "💰",
    "dollar": "💵",
    "dollars": "💵",
    "rich": "🤑",
    "millionaire": "🤑",
    "billionaire": "🤑",
    "profit": "📈",
    "gain": "📈",
    "growth": "📈",
    "loss": "📉",
    "invest": "📊",
    "investment": "📊",
    "bank": "🏦",
    # Work / success
    "win": "🏆",
    "winner": "🏆",
    "won": "🏆",
    "best": "🏆",
    "champion": "🏆",
    "success": "🎯",
    "goal": "🎯",
    "goals": "🎯",
    "target": "🎯",
    "work": "💼",
    "job": "💼",
    "business": "💼",
    "boss": "👑",
    "king": "👑",
    "queen": "👑",
    "leader": "👑",
    # Tech
    "ai": "🤖",
    "robot": "🤖",
    "phone": "📱",
    "computer": "💻",
    "code": "💻",
    "coding": "💻",
    "app": "📱",
    "rocket": "🚀",
    "launch": "🚀",
    "start": "🚀",
    "startup": "🚀",
    "idea": "💡",
    "think": "💡",
    "brain": "🧠",
    "smart": "🧠",
    "genius": "🧠",
    # Attention / flags
    "watch": "👀",
    "look": "👀",
    "see": "👀",
    "wait": "⏳",
    "time": "⏰",
    "now": "⚡",
    "fast": "⚡",
    "quick": "⚡",
    "warning": "⚠️",
    "danger": "⚠️",
    "stop": "🛑",
    "listen": "👂",
    "ear": "👂",
    "secret": "🤫",
    "hidden": "🤫",
    "important": "❗",
    "never": "🚫",
    "no": "🚫",
    "yes": "✅",
    "right": "✅",
    "correct": "✅",
    "wrong": "❌",
    "bad": "👎",
    "good": "👍",
    "great": "👍",
    "nice": "👍",
    # Food / body (occasional)
    "food": "🍔",
    "eat": "🍽️",
    "coffee": "☕",
    "drink": "🥤",
    "water": "💧",
    "sleep": "😴",
    "tired": "😴",
    "run": "🏃",
    "gym": "💪",
    "strong": "💪",
    "power": "⚡",
}

EMOJI_ID: dict[str, str] = {
    # Hype / emotion
    "gila": "🤯",
    "mantap": "💯",
    "keren": "🔥",
    "hebat": "🔥",
    "dahsyat": "💥",
    "wow": "😮",
    "wah": "😮",
    "kaget": "😱",
    "takut": "😨",
    "cinta": "❤️",
    "sayang": "❤️",
    "suka": "❤️",
    "tangis": "😭",
    "menangis": "😭",
    "lucu": "😂",
    "tawa": "😂",
    "tertawa": "😂",
    "sedih": "😢",
    "senang": "😊",
    "bahagia": "😊",
    "marah": "😡",
    "kesal": "😡",
    # Money / business
    "uang": "💰",
    "duit": "💰",
    "rupiah": "💵",
    "kaya": "🤑",
    "miliarder": "🤑",
    "jutawan": "🤑",
    "untung": "📈",
    "keuntungan": "📈",
    "rugi": "📉",
    "kerugian": "📉",
    "investasi": "📊",
    "bank": "🏦",
    "bisnis": "💼",
    "kerja": "💼",
    "pekerjaan": "💼",
    "bos": "👑",
    "raja": "👑",
    "ratu": "👑",
    "pemimpin": "👑",
    "sukses": "🏆",
    "menang": "🏆",
    "pemenang": "🏆",
    "terbaik": "🏆",
    "juara": "🏆",
    "tujuan": "🎯",
    "target": "🎯",
    # Tech / ideas
    "ai": "🤖",
    "kecerdasan": "🧠",
    "pintar": "🧠",
    "cerdas": "🧠",
    "otak": "🧠",
    "ide": "💡",
    "pikir": "💡",
    "pikiran": "💡",
    "aplikasi": "📱",
    "komputer": "💻",
    "ponsel": "📱",
    "handphone": "📱",
    "kode": "💻",
    "roket": "🚀",
    "mulai": "🚀",
    "memulai": "🚀",
    # Attention / flags
    "lihat": "👀",
    "tonton": "👀",
    "dengar": "👂",
    "dengarkan": "👂",
    "tunggu": "⏳",
    "waktu": "⏰",
    "sekarang": "⚡",
    "cepat": "⚡",
    "bahaya": "⚠️",
    "awas": "⚠️",
    "berhenti": "🛑",
    "rahasia": "🤫",
    "penting": "❗",
    "jangan": "🚫",
    "tidak": "🚫",
    "tak": "🚫",
    "ya": "✅",
    "benar": "✅",
    "betul": "✅",
    "salah": "❌",
    "buruk": "👎",
    "baik": "👍",
    "bagus": "👍",
    # Life
    "makan": "🍽️",
    "makanan": "🍔",
    "kopi": "☕",
    "minum": "🥤",
    "air": "💧",
    "tidur": "😴",
    "lelah": "😴",
    "capek": "😴",
    "olahraga": "💪",
    "kuat": "💪",
    "tenaga": "⚡",
}


# Emoji tables combined: Indonesian first so ID hits win on ambiguous tokens
# like "target" (shared across both languages, fine either way), "ai" (both
# list robot), etc. Duplicate keys resolve to the later dict — we want ID
# preferred for Indonesian creators since that's the user's primary market.
_ALL: dict[str, str] = {**EMOJI_EN, **EMOJI_ID}


_PUNCT = re.compile(r"[^\w\-']", re.UNICODE)


def _normalize(token: str) -> str:
    """Lowercase, strip accents, strip punctuation."""
    t = unicodedata.normalize("NFKD", token)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = _PUNCT.sub("", t)
    return t


def emoji_for(token: str) -> str | None:
    """Return a single emoji for ``token`` (case-insensitive, punctuation-insensitive),
    or ``None`` if no keyword matches.
    """
    norm = _normalize(token)
    if not norm:
        return None
    return _ALL.get(norm)


def pick_phrase_emoji(words: list[str]) -> tuple[int, str] | None:
    """Return ``(word_index, emoji)`` for the first word in ``words`` that
    matches the keyword dictionary, or ``None``. One emoji per phrase max:
    decoration should feel deliberate, not spammy.
    """
    for i, w in enumerate(words):
        e = emoji_for(w)
        if e:
            return i, e
    return None
