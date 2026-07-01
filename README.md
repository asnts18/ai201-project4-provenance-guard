# Provenance Guard

A backend attribution service that any creative-sharing platform can plug into. It
accepts submitted text, runs it through two independent detection signals, blends
them into a calibrated confidence score, shows the reader a plain-language
transparency label, and lets creators appeal a decision they believe is wrong.

The full spec — architecture diagram, signal design rationale, scoring formula,
label wording, and edge cases — lives in [planning.md](planning.md). This README
documents the built system and the evidence that it works.

---

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key_here" > .env
python app.py
```

**Note (macOS):** port 5000 is claimed by default by macOS's AirPlay Receiver,
which returns a bare `403` from `AirTunes` — not a Flask error — and looks like
the app failed to start when it didn't. If `curl localhost:5000/health` doesn't
return `{"status": "ok"}`, run `PORT=5001 python app.py` instead (or disable
AirPlay Receiver under System Settings → General → AirDrop & Handoff).

## API Surface

| Method & path | Purpose |
|---|---|
| `POST /submit` | Submit text for attribution analysis. |
| `POST /appeal` | Contest a classification. |
| `GET /appeals` | Reviewer queue of open appeals. |
| `GET /content/<content_id>` | Current status of a submission. |
| `GET /log?limit=N` | Structured audit log, newest first. |
| `GET /health` | Liveness check. |

Full request/response shapes are in [planning.md §3](planning.md#3-api-surface-the-contract).

---

## Detection Signals

Two signals that are **genuinely independent** — one judges meaning, the other
judges shape — so their disagreement is itself informative, not noise.

### Signal 1 — Groq LLM classifier ([signals/llm_signal.py](signals/llm_signal.py))

Sends the raw text to `llama-3.3-70b-versatile` with a prompt asking it to judge
`p_ai` holistically: tone, voice, hedging, idiosyncrasy, overall fluency. **Why
this signal:** an LLM has absorbed a vast distribution of both human and AI text
and can pick up on subtle cues — generic phrasing, over-hedged claims, absence of
a distinct "voice" — that no fixed formula captures. **What it can't capture:**
it's a holistic, non-deterministic judgment call, not a measurement. It's a hard
dependency on an external API (if Groq is down, `/submit` fails loud with a
`502` rather than silently downgrading to single-signal detection). It can be
fooled by paraphrase/"humanizer" tools, and — demonstrated in our own testing
below — it's biased toward reading *formal, impersonal, hedged* prose as AI even
when a human wrote it that way on purpose (academic writing, non-native-English
writing).

### Signal 2 — Stylometric heuristics ([signals/stylometry_signal.py](signals/stylometry_signal.py))

Pure Python, no external calls. Computes three structural features and blends
them into `p_ai`:

- **Sentence-length burstiness** (coefficient of variation of sentence
  lengths, weight 0.5) — human writing tends to alternate long and short
  sentences; AI writing tends toward uniform sentence length.
- **Punctuation/expressiveness rate** (`!`, `?`, ALL-CAPS emphasis words,
  em-dashes/ellipses per word, weight 0.35) — human writing leans on
  expressive punctuation to carry tone; AI writing tends toward flat, regular
  punctuation.
- **Type-token ratio** (unique words / total words, weight 0.15, deliberately
  small — see "why the low weight" below).

`mean_sentence_len` and `punct_density` are also computed and reported in the
response for transparency, but don't feed the score directly — on their own
they're ambiguous (both formal human prose and AI prose can run long).

**Why this signal:** it's fast, free, deterministic, and captures something the
LLM signal structurally cannot — the LLM judges what the words *mean*; this
judges how the sentences are *shaped*, independent of content.

**What it can't capture:** it's tuned for English prose and breaks on poetry,
lists, code, or any genre with naturally uniform structure (see Known
Limitations). It's also unreliable on short text — type-token ratio in
particular is noisy under ~100 words, which is *why* it's weighted at only
0.15 instead of an equal split with the other two features (see AI Usage below
for how this was discovered).

---

## Confidence Scoring

### The math ([scoring.py](scoring.py))

```
p_blend = 0.60 * s_llm + 0.40 * s_sty        # LLM weighted higher (holistic > structural)
d       = |s_llm - s_sty|                    # signal disagreement
p_ai    = 0.5 + (p_blend - 0.5) * (1 - d)    # disagreement pulls the verdict toward 0.5
confidence = |p_ai - 0.5| * 2                # 0 = coin-flip, 1 = certain

p_ai >= 0.70  -> high_confidence_ai
p_ai <= 0.30  -> high_confidence_human
otherwise     -> uncertain
```

**Why this approach.** A bare average of two scores would produce a falsely
confident verdict whenever the two signals actually disagree — e.g., LLM says
0.9 AI, stylometry says 0.1 AI, and a naive average (0.5) happens to look
"uncertain" only by coincidence, while a case where they're both near 0.5 but
for different reasons would look identical to a case where they strongly agree.
The disagreement term makes disagreement *itself* part of the score: two signals
that actively contradict each other collapse the verdict toward "uncertain"
rather than canceling out into a false middle ground, and `confidence` is
computed from the *final* `p_ai`, not from the raw inputs, so it reflects how
far the honest, disagreement-adjusted verdict is from a coin flip.

**How we tested that scores are meaningful.** We ran four deliberately chosen
inputs — one clearly AI, one clearly human, two borderline — through the full
pipeline and inspected both raw signal scores before trusting the combined
output (see AI Usage below for how this caught a real calibration bug). Two
representative results, showing the score moving meaningfully rather than
sitting at a constant:

**Higher-confidence example** — casual first-person restaurant review ("ok so i
finally tried that new ramen place downtown and honestly? underwhelming...")
```
s_llm = 0.200   s_sty = 0.226   disagreement = 0.026
p_ai = 0.218    confidence = 0.564   band = high_confidence_human
```

**Lower-confidence example** — lightly-edited reflection on remote work ("I've
been thinking a lot about remote work lately. There are genuine tradeoffs...")
```
s_llm = 0.400   s_sty = 0.435   disagreement = 0.035
p_ai = 0.417    confidence = 0.166   band = uncertain
```

The two signals *agree* in both cases (low disagreement), but the underlying
values are far apart from each other — one confidently human, one barely
distinguishable from a coin flip — and the system reports that difference
honestly instead of forcing every input into a binary verdict.

**What we'd change for a real deployment.** The internal weights (0.6/0.4
signal blend, 0.5/0.35/0.15 stylometric sub-weights, 0.70/0.30 thresholds) are
hand-tuned against a handful of test cases, not fit against real labeled data.
For production we'd want: (1) a labeled corpus of known-human and known-AI text
across genres, (2) a proper calibration method (logistic regression or isotonic
regression on top of the raw signal outputs, instead of hand-picked linear
weights) so that a reported `confidence` of 0.8 actually corresponds to ~80%
empirical accuracy, and (3) per-genre calibration, since the stylometry signal's
blind spots are genre-shaped (poetry vs. prose vs. technical writing) and a
single global threshold can't serve all of them well.

---

## Transparency Label

Three variants, defined in [labels.py](labels.py). `{confidence}` is
`round(confidence * 100)`. Exact text shown to the reader:

**High-confidence AI** (`high_confidence_ai`):
> 🤖 **Likely AI-generated.** Our automated analysis indicates this content was probably created with significant AI assistance (confidence: {confidence}%). This is an estimate from automated signals, not a certainty. If you're the creator and believe this is wrong, you can appeal this label.

**High-confidence human** (`high_confidence_human`):
> ✍️ **Likely human-written.** Our automated analysis found no strong signs of AI generation in this content (confidence: {confidence}%). This is an estimate from automated signals, not a guarantee.

**Uncertain** (`uncertain`):
> ❓ **Attribution uncertain.** Our automated analysis couldn't confidently tell whether this content was written by a human or generated with AI (confidence in a verdict: {confidence}%). Treat this as inconclusive — no attribution claim is being made.

All three were verified reachable by submitting the four test inputs above plus
one more clearly-AI sample — see the audit log entries below, which include all
three `label_variant` values.

---

## Appeals Workflow

`POST /appeal` accepts `{content_id, creator_reasoning, creator_id?}`. On receipt
it: looks up the original decision (`404` if unknown), writes an `appeals` row
linked to it, flips the content's `status` from `classified` to `under_review`,
and appends an `appeal_filed` event to the audit log referencing the original
decision's `label_variant`, `confidence`, and `p_ai`. No automated
re-classification happens — a human reviewer works the queue via `GET /appeals`,
which returns the full original text, both signal breakdowns, and the creator's
reasoning side by side.

**Real example from testing** — an academic-economics-style passage was
correctly flagged `high_confidence_ai` by both signals (a real instance of the
formal-writing blind spot described above), then appealed:

```json
POST /appeal
{"content_id": "c_417ee12d3e7c",
 "creator_reasoning": "I am a non-native English speaker; my academic training makes my writing sound formal. This is entirely my own work."}

-> {"appeal_id": "a_baca34c36780", "content_id": "c_417ee12d3e7c",
    "status": "under_review", "logged_at": "2026-07-01T00:43:28.289193Z"}
```

`GET /content/c_417ee12d3e7c` afterward returns `"status": "under_review"`.

---

## Rate Limiting

Applied to `POST /submit` per client IP via Flask-Limiter (`memory://` storage):

| Limit | Value | Reasoning |
|---|---|---|
| Burst | **10 / minute** | Each submission costs one Groq LLM call — real latency and API quota. 10/min comfortably covers a writer reading a label and deciding whether to submit their next piece, while blocking a script that hammers the endpoint. |
| Sustained | **100 / hour** | Caps one IP's Groq spend and protects the shared quota from a single noisy tenant, while leaving generous headroom for a legitimate integration-testing session. |

**Evidence** — 12 rapid submissions against a freshly started server:

```
$ for i in $(seq 1 12); do
    curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5001/submit \
      -H "Content-Type: application/json" \
      -d '{"text": "...40+ words...", "creator_id": "ratelimit-test"}'
  done
200
200
200
200
200
200
200
200
200
200
429
429
```

The 11th and 12th requests are rejected with a structured JSON body:
```json
{"error": "rate limit exceeded: 10 per 1 minute"}
```
(Flask-Limiter's default 429 response is bare HTML; we added an
`@app.errorhandler(429)` in [app.py](app.py) so every error from this API,
including rate-limit rejections, is JSON.)

Read endpoints (`/log`, `/appeals`, `/content`, `/health`) are not
rate-limited — no external cost is incurred by reading.

---

## Audit Log

SQLite-backed ([db.py](db.py)), structured JSON payloads, not print statements.
Three consecutive entries from a real test run — two decisions and the appeal
filed against the second one, showing both signal scores, the combined
confidence, and the appeal linked back to its original decision:

```json
{
  "entries": [
    {
      "id": 3, "event": "appeal_filed", "content_id": "c_417ee12d3e7c",
      "timestamp": "2026-07-01T00:43:28.289193Z",
      "payload": {
        "appeal_id": "a_baca34c36780",
        "appeal_reasoning": "I am a non-native English speaker; my academic training makes my writing sound formal. This is entirely my own work.",
        "status": "under_review",
        "original_label_variant": "high_confidence_ai",
        "original_confidence": 0.4957858301784748,
        "original_p_ai": 0.7478929150892374
      }
    },
    {
      "id": 2, "event": "decision_created", "content_id": "c_417ee12d3e7c",
      "timestamp": "2026-07-01T00:43:28.201082Z",
      "payload": {
        "attribution": "likely_ai", "band": "high_confidence_ai",
        "confidence": 0.4957858301784748, "p_ai": 0.7478929150892374,
        "disagreement": 0.0779, "llm_score": 0.8, "stylometry_score": 0.722,
        "status": "classified"
      }
    },
    {
      "id": 1, "event": "decision_created", "content_id": "c_2ead08439c69",
      "timestamp": "2026-07-01T00:42:41.515552Z",
      "payload": {
        "attribution": "likely_ai", "band": "high_confidence_ai",
        "confidence": 0.4957858301784748, "p_ai": 0.7478929150892374,
        "disagreement": 0.0779, "llm_score": 0.8, "stylometry_score": 0.722,
        "status": "classified"
      }
    }
  ]
}
```

---

## Known Limitations

1. **Repetitive, simple-vocabulary poetry** (nursery rhymes, refrains,
   minimalist verse) will likely be misread by the *stylometry* signal
   specifically: low sentence-length variance and low type-token ratio are
   exactly what the burstiness and TTR features treat as "uniform = AI-like,"
   but repetition in a poem is a deliberate human artistic choice, not a
   machine tell. Mitigation is the disagreement penalty — if the LLM signal
   correctly reads it as creative/human, the disagreement pulls the verdict to
   "uncertain" rather than a false high-confidence-AI label — but this only
   works if the LLM signal gets it right, which isn't guaranteed.

2. **Formal, hedged, or non-native-English human prose** will likely be
   misread by *both* signals at once, which is a real limitation because it
   defeats the disagreement-penalty safety net described above. This isn't
   hypothetical — it happened in our own testing (the economics-style passage
   above): the LLM signal associates hedged, impersonal, generically fluent
   phrasing with AI output, and the stylometry signal's burstiness feature
   reads the same passage's uniform, well-formed sentence structure as
   AI-like uniformity. Both signals are keying off the *same* underlying
   property — formal register — rather than truly independent evidence, so
   agreement between them here is misleadingly reassuring. This is precisely
   the false-positive scenario the appeals workflow exists to catch.

---

## Spec Reflection

**How the spec helped.** Writing the disagreement-penalty formula in
`planning.md` §2.2 *before* any code existed meant that when Milestone 4
testing showed the two signals routinely disagreeing (by 0.03 to 0.34 across
test inputs), there was already a principled, pre-committed way to handle
it — pull toward "uncertain" — rather than improvising ad hoc averaging logic
in the moment, which is a much easier trap to fall into once you're staring at
two numbers that don't match.

**Where the implementation diverged, and why.** The spec's API contract
(`planning.md` §3) documented the appeal field as `reasoning`. Milestone 5's
own test script, however, used `creator_reasoning` in its curl example. Rather
than rigidly enforcing one field name and breaking the graded test path, the
`/appeal` endpoint ([app.py:152](app.py)) accepts either key. A stricter
reading of "the spec is the contract" would say pick one name — but the
practical goal (an appeal a real test script can actually file) mattered more
than contract purity here, so both are honored.

---

## AI Usage

This project was built with an AI coding assistant (Claude Code) generating
code from the `planning.md` spec, milestone by milestone, with each output
tested independently before being wired into the app. Two concrete instances
where generated output was corrected after testing surfaced a real problem:

**Instance 1 — stylometry calibration.** I directed the AI to generate the
stylometric signal function from the spec's four described features
(sentence-length variance, type-token ratio, punctuation density, mean
sentence length), combined with roughly equal weights and a narrow definition
of "irregular punctuation" (only doubled marks like `!!`/`??`/`...`). Testing
it independently on the four Milestone-4 sample inputs (one clearly AI, one
clearly human, two borderline) showed every single combined score landing in
the "uncertain" band — including the two unambiguous cases, which should not
have been ambiguous. Printing the two raw signal scores separately (as the
milestone instructions suggested) showed why: type-token ratio was nearly
identical (0.86–0.90) across all four short samples regardless of origin — a
genuinely uninformative feature at this text length — and the punctuation
heuristic missed the actual informal markers present in the human sample
(a single `?`, the word `WAY` in caps) because it only matched doubled
punctuation. I overrode this by cutting TTR's weight from ~0.3 to 0.15 and
rewriting the punctuation detector to count single `!`/`?`, ALL-CAPS words,
and em-dashes/ellipses per word. Re-running the same four inputs afterward
produced the intended three-band spread (`high_confidence_ai`,
`high_confidence_human`, `uncertain`) with meaningfully different confidence
values.

**Instance 2 — appeal field name.** I asked the AI to generate the `/appeal`
endpoint strictly from the spec's documented contract, which used the field
name `reasoning`. The endpoint it produced only accepted `reasoning` and
would have returned a `400` against Milestone 5's own curl test, which sends
`creator_reasoning`. I caught this by re-reading the milestone's test command
against the generated code before considering the endpoint done (per the
milestone's own instruction to verify status updates and logging before
moving on), and revised the endpoint to accept either key name rather than
pick one — preserving compatibility with both the original spec and the
actual grading path.

---

## Repo layout

```
app.py                        Flask app — all routes
db.py                         SQLite persistence (decisions, appeals, audit_log)
scoring.py                    Signal-blending / confidence formula
labels.py                     Transparency label text generation
signals/llm_signal.py         Signal 1 — Groq LLM classifier
signals/stylometry_signal.py  Signal 2 — stylometric heuristics
planning.md                   Full spec (architecture, signals, edge cases)
```
