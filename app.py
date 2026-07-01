"""Provenance Guard — Flask API.

Full pipeline: POST /submit runs both signals, blends them into a confidence
score/band (planning.md §2.2), renders the matching transparency label
(§2.3), and persists the decision to the audit log. POST /appeal implements
the appeals workflow (§2.4). Rate limiting is applied per §4.
"""
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from db import (
    append_audit,
    get_appeals,
    get_decision,
    get_log,
    init_db,
    insert_appeal,
    insert_decision,
    update_status,
)
from labels import generate_label
from scoring import combine_signals
from signals.llm_signal import LLMSignalError, get_llm_signal
from signals.stylometry_signal import get_stylometry_signal

MIN_WORDS = 40

app = Flask(__name__)
init_db()

# Rate limiting (planning.md §4): 10/minute covers a real writer submitting
# their own work between reads; 100/hour caps a single IP's Groq spend and
# protects the shared quota from one noisy tenant.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({"error": f"rate limit exceeded: {e.description}"}), 429


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per hour")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = data.get("creator_id")

    if not text:
        return jsonify({"error": "'text' field is required and cannot be empty"}), 400

    if len(text.split()) < MIN_WORDS:
        return jsonify({
            "error": f"text must contain at least {MIN_WORDS} words for a reliable "
                      f"attribution signal (got {len(text.split())})"
        }), 400

    try:
        llm_result = get_llm_signal(text)
    except LLMSignalError as exc:
        return jsonify({"error": f"LLM signal unavailable: {exc}"}), 502

    stylometry_result = get_stylometry_signal(text)

    content_id = f"c_{uuid.uuid4().hex[:12]}"
    timestamp = _now_iso()
    llm_p_ai = llm_result["p_ai"]
    sty_p_ai = stylometry_result["p_ai"]

    combined = combine_signals(llm_p_ai, sty_p_ai)
    p_ai = combined["p_ai"]
    confidence = combined["confidence"]
    band = combined["band"]
    attribution = combined["classification"]

    label = generate_label(band, confidence)
    status = "classified"

    decision = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "created_at": timestamp,
        "llm_p_ai": llm_p_ai,
        "llm_reasoning": llm_result.get("reasoning"),
        "stylometry_p_ai": sty_p_ai,
        "stylometry_features": stylometry_result["features"],
        "p_ai": p_ai,
        "confidence": confidence,
        "label_variant": label["variant"],
        "label_text": label["text"],
        "status": status,
        "model": "llama-3.3-70b-versatile",
    }
    insert_decision(decision)

    append_audit(
        event="decision_created",
        content_id=content_id,
        timestamp=timestamp,
        payload={
            "creator_id": creator_id,
            "attribution": attribution,
            "confidence": confidence,
            "p_ai": p_ai,
            "disagreement": combined["disagreement"],
            "llm_score": llm_p_ai,
            "stylometry_score": sty_p_ai,
            "band": band,
            "status": status,
        },
    )

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": confidence,
        "p_ai": p_ai,
        "signals": {
            "llm": {"p_ai": llm_p_ai, "reasoning": llm_result.get("reasoning")},
            "stylometry": {"p_ai": sty_p_ai, "features": stylometry_result["features"]},
        },
        "label": label,
        "status": status,
        "timestamp": timestamp,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    reasoning = (data.get("creator_reasoning") or data.get("reasoning") or "").strip()
    creator_id = data.get("creator_id")

    if not content_id or not reasoning:
        return jsonify({"error": "'content_id' and 'creator_reasoning' are required"}), 400

    decision = get_decision(content_id)
    if not decision:
        return jsonify({"error": f"no decision found for content_id '{content_id}'"}), 404

    appeal_id = f"a_{uuid.uuid4().hex[:12]}"
    timestamp = _now_iso()

    insert_appeal({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": creator_id,
        "reasoning": reasoning,
        "status": "open",
        "filed_at": timestamp,
    })
    update_status(content_id, "under_review")

    append_audit(
        event="appeal_filed",
        content_id=content_id,
        timestamp=timestamp,
        payload={
            "appeal_id": appeal_id,
            "creator_id": creator_id,
            "appeal_reasoning": reasoning,
            "status": "under_review",
            "original_label_variant": decision["label_variant"],
            "original_confidence": decision["confidence"],
            "original_p_ai": decision["p_ai"],
        },
    )

    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "logged_at": timestamp,
    })


@app.route("/appeals", methods=["GET"])
def appeals_queue():
    queue = []
    for a in get_appeals():
        decision = get_decision(a["content_id"]) or {}
        queue.append({
            "appeal_id": a["appeal_id"],
            "content_id": a["content_id"],
            "creator_id": a["creator_id"],
            "reasoning": a["reasoning"],
            "status": a["status"],
            "filed_at": a["filed_at"],
            "original_text": decision.get("text"),
            "signals": {
                "llm": {"p_ai": decision.get("llm_p_ai"), "reasoning": decision.get("llm_reasoning")},
                "stylometry": {
                    "p_ai": decision.get("stylometry_p_ai"),
                    "features": decision.get("stylometry_features"),
                },
            },
            "p_ai": decision.get("p_ai"),
            "confidence": decision.get("confidence"),
            "label_variant": decision.get("label_variant"),
            "content_status": decision.get("status"),
        })
    return jsonify({"appeals": queue})


@app.route("/content/<content_id>", methods=["GET"])
def content(content_id):
    decision = get_decision(content_id)
    if not decision:
        return jsonify({"error": f"no decision found for content_id '{content_id}'"}), 404
    return jsonify({
        "content_id": decision["content_id"],
        "status": decision["status"],
        "attribution": decision["label_variant"],
        "confidence": decision["confidence"],
        "p_ai": decision["p_ai"],
    })


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": get_log(limit)})


if __name__ == "__main__":
    import os

    # macOS AirPlay Receiver claims port 5000 by default (returns a bare 403,
    # not a Flask error) — override with PORT=5001 if /health doesn't respond.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
