"""Transparency label generation — verbatim text from planning.md §2.3.

Exactly three variants, selected by the band returned from scoring.py.
`{confidence}` is filled in with round(confidence * 100).
"""

_TEMPLATES = {
    "high_confidence_ai": (
        "\U0001F916 **Likely AI-generated.** Our automated analysis indicates this content was "
        "probably created with significant AI assistance (confidence: {pct}%). "
        "This is an estimate from automated signals, not a certainty. If you're the "
        "creator and believe this is wrong, you can appeal this label."
    ),
    "high_confidence_human": (
        "✍️ **Likely human-written.** Our automated analysis found no strong signs of AI "
        "generation in this content (confidence: {pct}%). This is an estimate from "
        "automated signals, not a guarantee."
    ),
    "uncertain": (
        "❓ **Attribution uncertain.** Our automated analysis couldn't confidently tell "
        "whether this content was written by a human or generated with AI (confidence in "
        "a verdict: {pct}%). Treat this as inconclusive — no attribution claim is being made."
    ),
}


def generate_label(band, confidence):
    """Return {"variant": band, "text": <exact label text>} for the given band."""
    template = _TEMPLATES.get(band, _TEMPLATES["uncertain"])
    pct = round(confidence * 100)
    return {"variant": band, "text": template.format(pct=pct)}
