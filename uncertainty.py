"""STR-8: how sure Whisper was about a final line, from the statistics it already returns.

A line is unsure when its speech-weighted avg_logprob is low, when Whisper itself thinks it may be no
speech, or when the text repeats itself (compression_ratio). Words are unsure below WORD_PROB.
Thresholds are first guesses (see the tracker: calibrate on QA-2/QA-9 before turning this on by default)."""
import math

import clarify

LINE_LOGPROB = -0.8      # avg_logprob below this: the line is unsure
LINE_NO_SPEECH = 0.5     # no_speech_prob above this (with a weak logprob) : unsure
NO_SPEECH_LOGPROB = -0.5
LINE_COMPRESSION = 2.4   # Whisper's own repetition threshold
WORD_PROB = clarify.LOW_PROB   # the same words VOC-9 puts in the "Did they say ...?" queue


def _num(x, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else x


def line_uncertainty(segments):
    """{'unsure': bool, 'confidence': 0..1, 'why': [...], 'words': [unsure words]} for one final line,
    or None when there is nothing to judge."""
    segs = [s for s in segments or () if (getattr(s, "text", "") or "").strip()]
    if not segs:
        return None
    weights = [max(_num(getattr(s, "end", 0), 0) - _num(getattr(s, "start", 0), 0), 0.1) for s in segs]
    total = sum(weights)
    logprob = sum(w * _num(getattr(s, "avg_logprob", 0), 0) for s, w in zip(segs, weights)) / total
    no_speech = max(_num(getattr(s, "no_speech_prob", 0), 0) for s in segs)
    compression = max(_num(getattr(s, "compression_ratio", 0), 0) for s in segs)
    why = []
    if logprob < LINE_LOGPROB:
        why.append("logprob")
    if no_speech > LINE_NO_SPEECH and logprob < NO_SPEECH_LOGPROB:
        why.append("no_speech")
    if compression > LINE_COMPRESSION:
        why.append("repeats")
    words = [w.word.strip() for s in segs for w in (getattr(s, "words", None) or ())
             if w.word.strip() and _num(getattr(w, "probability", 1), 1) < WORD_PROB]
    return {"unsure": bool(why), "confidence": round(math.exp(min(logprob, 0.0)), 3), "why": why,
            "words": words}
