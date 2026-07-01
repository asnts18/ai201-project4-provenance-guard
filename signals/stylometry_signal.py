"""Signal 2: stylometric heuristics — structural properties, pure Python.

Computes sentence-length burstiness, type-token ratio, and punctuation
irregularity, then maps each to an AI-ward probability in [0,1] and blends
them into a single stylometric p_ai. Mean sentence length is reported as a
diagnostic feature but does not feed the score directly (it's ambiguous on
its own — both formal human prose and AI prose can run long).
"""
import re
import statistics

# Calibration constants (see README for how these were chosen/tuned).
CV_SATURATION = 1.0          # coefficient of variation >= this -> fully "bursty" (human)
TTR_LOW = 0.35                # type-token ratio <= this -> fully repetitive (AI-ward)
TTR_HIGH = 0.75               # type-token ratio >= this -> fully diverse (human-ward)
EXPRESSIVENESS_SATURATION = 0.04  # informal marks per word >= this -> fully human-ward

# TTR is weighted lightly: on short samples (a paragraph or two) it barely
# discriminates AI from human — a documented blind spot (planning.md §2.1) —
# so it's kept as a reported feature but not trusted heavily in the score.
WEIGHTS = {"burstiness": 0.5, "ttr": 0.15, "punctuation": 0.35}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z']+")
_ELLIPSIS_DASH_RE = re.compile(r"(\.\.\.|—|--)")
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{3,}\b")
_PUNCT_CHARS = set(".,;:!?—-\"'()")


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _split_sentences(text):
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _words(text):
    return _WORD_RE.findall(text.lower())


def get_stylometry_signal(text):
    """Return {"p_ai": float in [0,1], "features": {...}} for the given text."""
    sentences = _split_sentences(text)
    all_words = _words(text)

    sentence_lengths = [len(_words(s)) for s in sentences]
    sentence_lengths = [n for n in sentence_lengths if n > 0]

    mean_sentence_len = statistics.mean(sentence_lengths) if sentence_lengths else 0.0
    std_sentence_len = statistics.pstdev(sentence_lengths) if len(sentence_lengths) > 1 else 0.0
    variance_sentence_len = std_sentence_len ** 2
    cv = (std_sentence_len / mean_sentence_len) if mean_sentence_len > 0 else 0.0

    type_token_ratio = (len(set(all_words)) / len(all_words)) if all_words else 0.0

    punct_chars = sum(1 for c in text if c in _PUNCT_CHARS)
    punct_density = (punct_chars / len(all_words)) if all_words else 0.0

    # "Informal marks": expressive punctuation (!, ?), ellipses/dashes, and
    # emphasis ALL-CAPS words. AI prose tends toward flat, regular punctuation;
    # human prose leans on these markers to carry tone.
    informal_marks = (
        text.count("!")
        + text.count("?")
        + len(_ELLIPSIS_DASH_RE.findall(text))
        + len(_CAPS_WORD_RE.findall(text))
    )
    expressiveness_rate = (informal_marks / len(all_words)) if all_words else 0.0

    p_burst = _clamp01(1 - cv / CV_SATURATION)
    p_ttr = _clamp01((TTR_HIGH - type_token_ratio) / (TTR_HIGH - TTR_LOW))
    p_punct = _clamp01(1 - expressiveness_rate / EXPRESSIVENESS_SATURATION)

    p_ai = (
        WEIGHTS["burstiness"] * p_burst
        + WEIGHTS["ttr"] * p_ttr
        + WEIGHTS["punctuation"] * p_punct
    )

    return {
        "p_ai": _clamp01(p_ai),
        "features": {
            "sentence_len_variance": round(variance_sentence_len, 3),
            "type_token_ratio": round(type_token_ratio, 3),
            "punct_density": round(punct_density, 3),
            "mean_sentence_len": round(mean_sentence_len, 3),
            "coefficient_of_variation": round(cv, 3),
            "expressiveness_rate": round(expressiveness_rate, 3),
        },
    }
