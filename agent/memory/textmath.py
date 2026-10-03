"""Deterministic text checks used by the code-side verification gates.

These are deliberately simple and explainable: normalisation, token sets, containment
similarity, proper-noun tracking and coverage. No embeddings, no thresholds tuned to
make a demo pass -- the rules are stated in the README.
"""

from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being
    below between both but by can cannot could did do does doing done down during each few for from further
    had has have having he her here hers herself him himself his how i if in into is it its itself just me
    more most my myself no nor not now of off on once only or other our ours ourselves out over own same she
    should so some such than that the their them theirs themselves then there these they this those through to
    too under until up upon us use used very was we well were what when where which while who whom why will
        with you your yours yourself yourselves""".split()
)
# Third-person encoder pronouns: facts are rewritten as "User ...", so these carry no content.
STOPWORDS = STOPWORDS | frozenset({"user", "users", "assistant", "assistants", "bot", "ai"})

_PUNCT_MAP = {
    "\u2018": "", "\u2019": "", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "...",
}


def normalize(text: str) -> str:
    """Lowercase, NFKC, apostrophes folded away, punctuation stripped to spaces.

    Apostrophes are deleted rather than turned into spaces so "user's" and "users" normalise to
    the same token instead of producing a phantom "s".
    """
    for bad, good in _PUNCT_MAP.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKC", text)
    text = text.lower().replace("'", "")
    text = re.sub(r"[^0-9a-z\u00c0-\u024f\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stem_token(token: str) -> str:
    """Light, explainable inflection fold used by both gates.

    Possessive is stripped; a trailing plural -s is stripped unless the word ends in -ss/-us
    or is too short to be plural. No full stemmer: the rules are stated here and in the README
    so a rejection can always be explained in one sentence.
    """
    if token.endswith("'s"):
        token = token[:-2]
    if len(token) > 3 and not token.endswith(("ss", "us")) and token.endswith("s"):
        token = token[:-1]
    return token


def normalize_tokens(text: str) -> list[str]:
    return [stem_token(t) for t in normalize(text).split()]


def tokens(text: str, *, drop_stopwords: bool = True) -> list[str]:
    return [t for t in normalize(text).split() if not (drop_stopwords and t in STOPWORDS)]


def token_set(text: str, *, drop_stopwords: bool = True) -> frozenset[str]:
    return frozenset(stem_token(t) for t in tokens(text, drop_stopwords=drop_stopwords))


def similarity(a: str, b: str) -> float:
    """Token-set containment over folded tokens: |A n B| / min(|A|, |B|).

    Containment (rather than Jaccard) is used so that a strict superset of another fact --
    "lives in Lisbon" vs "lives in lisbon portugal" -- reads as the same fact. Equality after
    normalisation + inflection fold always scores 1.0.
    """
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 1.0 if normalize(a) == normalize(b) else 0.0
    shared = len(ta & tb)
    return shared / min(len(ta), len(tb))


def proper_nouns(text: str) -> set[str]:
    """Capitalised-initial words that are not at a sentence start.

    Surface form is preserved: 'Marco' and 'marco' are the same name but only the capitalised
    surface form is treated as a proper noun, which is what makes the merge gate catch
    invented names.
    """
    found: set[str] = set()
    for match in re.finditer(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9'\u00c0-\u024f]{1,})", text):
        word = match.group(1)
        prefix = text[: match.start()].rstrip()
        if prefix.endswith((".", "!", "?", ":", '"', "'")) or not prefix:
            continue  # sentence-initial capitalisation is not evidence of a name
        if word.lower() in STOPWORDS:
            continue
        found.add(word)
    return found


def coverage(claim: str, sources: list[str]) -> tuple[float, list[str]]:
    """Fraction of the claim's content tokens that appear in at least one source.

    The claim is checked on non-stopword, inflection-folded tokens; the source vocabulary keeps
    stopwords, because a rewrite may legitimately promote a function word into content
    ("use Python" -> "uses Python"). Returns (coverage, missing_tokens).
    """
    claim_norm = " ".join(stem_token(t) for t in normalize(claim).split())
    source_norms = [" ".join(stem_token(t) for t in normalize(s).split()) for s in sources]
    if claim_norm and any(claim_norm in s for s in source_norms):
        return 1.0, []
    content = [t for t in claim_norm.split() if t not in STOPWORDS and stem_token(t) not in STOPWORDS]
    if not content:
        return 1.0, []
    vocab: set[str] = set()
    for s in source_norms:
        vocab.update(s.split())
    missing = sorted({t for t in content if t not in vocab})
    return (len(content) - len(missing)) / len(content), missing


def find_duplicate_pairs(items: list[dict], threshold: float = 0.9) -> list[tuple[str, str, float]]:
    """Code-side duplicate detection: (loserId, keeperId, similarity), oldest fact keeps."""
    pairs = []
    by_age = sorted(items, key=lambda i: (i["createdAt"], i["itemId"]))
    for i, a in enumerate(by_age):
        for b in by_age[i + 1 :]:
            if a["block"] != b["block"]:
                continue
            score = similarity(a["text"], b["text"])
            if score >= threshold:
                pairs.append((b["itemId"], a["itemId"], score))
    return pairs
