"""Confidence scoring — blends the two signals per planning.md §2.2.

    p_blend = 0.60 * s_llm + 0.40 * s_sty
    d       = |s_llm - s_sty|                  # signal disagreement
    p_ai    = 0.5 + (p_blend - 0.5) * (1 - d)  # disagreement pulls toward 0.5
    confidence = |p_ai - 0.5| * 2

Bands: p_ai >= 0.70 -> high_confidence_ai
       p_ai <= 0.30 -> high_confidence_human
       otherwise    -> uncertain
"""

LLM_WEIGHT = 0.60
STYLOMETRY_WEIGHT = 0.40

AI_THRESHOLD = 0.70
HUMAN_THRESHOLD = 0.30


def combine_signals(s_llm, s_sty):
    p_blend = LLM_WEIGHT * s_llm + STYLOMETRY_WEIGHT * s_sty
    disagreement = abs(s_llm - s_sty)
    p_ai = 0.5 + (p_blend - 0.5) * (1 - disagreement)
    confidence = abs(p_ai - 0.5) * 2

    if p_ai >= AI_THRESHOLD:
        band = "high_confidence_ai"
        classification = "likely_ai"
    elif p_ai <= HUMAN_THRESHOLD:
        band = "high_confidence_human"
        classification = "likely_human"
    else:
        band = "uncertain"
        classification = "uncertain"

    return {
        "p_ai": p_ai,
        "confidence": confidence,
        "disagreement": disagreement,
        "band": band,
        "classification": classification,
    }
