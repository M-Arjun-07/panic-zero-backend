"""
guardian_mesh.py — Guardian Mesh Confidence Calculator
=======================================================
Sensor-fusion layer for PanicZero.

Combines four independent sensor streams:
  • visual   — from YOLO camera model (bounding-box detections)
  • acoustic  — from YAMNet audio model (sound classification)
  • motion    — derived from camera (fallen-person ratio, crowd count)
  • network   — alert-frequency / sensor-health heartbeat

Each stream contributes a normalised sub-score [0.0 – 1.0].
The sub-scores are fused with configurable weights into a single
confidence_score [0.0 – 1.0].  When the score crosses EMERGENCY_THRESHOLD
the engine sets trigger_emergency = True.

Outputs
-------
{
  "confidence_score":  0.87,          # 0.0 (calm) → 1.0 (critical)
  "trigger_emergency": True,
  "threat_level":      "CRITICAL",    # CALM / LOW / MODERATE / HIGH / CRITICAL
  "signal_breakdown": {
      "visual":   0.90,
      "acoustic": 0.85,
      "motion":   0.70,
      "network":  0.60
  },
  "dominant_threat":   "FIRE",        # which stream raised the alarm
  "recommendation":    "Dispatch emergency services immediately"
}

Integration points
------------------
  • POST /api/guardian/fuse           — fuse raw sensor payloads on demand
  • Internally called by process_crisis_alert() in main.py after detection
  • YOLO detector can POST its per-frame signal to update the sliding window
  • Audio detector can POST its per-chunk signal to update the sliding window
"""

import time
import math
from collections import deque
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────

# Fusion weights — must sum to 1.0
WEIGHTS = {
    "visual":   0.35,   # YOLO bounding-box detections (strongest signal)
    "acoustic": 0.30,   # YAMNet sound classification
    "motion":   0.20,   # Fallen-person ratio / crowd surge
    "network":  0.15,   # Alert frequency & sensor health
}

# Score thresholds → threat level labels
THREAT_LEVELS = [
    (0.00, 0.20, "CALM"),
    (0.20, 0.40, "LOW"),
    (0.40, 0.60, "MODERATE"),
    (0.60, 0.80, "HIGH"),
    (0.80, 1.01, "CRITICAL"),
]

# Score at or above this → trigger_emergency = True
EMERGENCY_THRESHOLD = 0.65

# Sliding-window size for network-frequency scoring
NETWORK_WINDOW_SECONDS = 60          # look back 60 s
MAX_ALERTS_IN_WINDOW   = 10          # 10 alerts/min = score 1.0

# Confidence maps: keywords in description → base visual/acoustic confidence
KEYWORD_CONFIDENCE = {
    # Visual / acoustic threat keywords → confidence boost
    "fire":        0.90,
    "smoke":       0.85,
    "flame":       0.88,
    "blaze":       0.92,
    "explosion":   0.95,
    "weapon":      0.90,
    "knife":       0.88,
    "gun":         0.92,
    "gunshot":     0.95,
    "fight":       0.75,
    "assault":     0.80,
    "violence":    0.78,
    "attack":      0.82,
    "unconscious": 0.80,
    "fallen":      0.75,
    "collapse":    0.78,
    "seizure":     0.82,
    "medical":     0.70,
    "help":        0.65,
    "crowd":       0.60,
    "panic":       0.65,
    "stampede":    0.85,
    "evacuation":  0.70,
}

# Severity → base confidence (linear interpolation from 1→5)
SEVERITY_CONFIDENCE = {1: 0.20, 2: 0.40, 3: 0.60, 4: 0.80, 5: 1.00}

# Threat-type → recommended action
RECOMMENDATIONS = {
    "FIRE":          "Activate fire suppression; evacuate via safest route; alert fire dept",
    "MEDICAL":       "Dispatch medical team immediately; call ambulance; clear path",
    "VIOLENCE":      "Deploy security; lock down area; alert police",
    "CROWD_CONTROL": "Initiate evacuation; deploy crowd-control team; alert police",
    "GENERAL":       "Dispatch security; assess situation; prepare escalation",
}

# ─────────────────────────────────────────────────────────────
#  Sliding-Window Alert Buffer  (for network signal scoring)
# ─────────────────────────────────────────────────────────────

_alert_timestamps: deque = deque(maxlen=500)  # global ring buffer


def record_alert_event(timestamp: Optional[float] = None):
    """
    Call this every time ANY sensor fires an alert.
    The network-signal scorer uses the resulting burst rate.
    """
    _alert_timestamps.append(timestamp or time.time())


def _network_signal_score() -> float:
    """
    Counts alerts fired in the last NETWORK_WINDOW_SECONDS seconds.
    Returns a normalised score in [0.0, 1.0].
    """
    now = time.time()
    cutoff = now - NETWORK_WINDOW_SECONDS
    recent = sum(1 for t in _alert_timestamps if t >= cutoff)
    return min(recent / MAX_ALERTS_IN_WINDOW, 1.0)


# ─────────────────────────────────────────────────────────────
#  Per-Stream Score Functions
# ─────────────────────────────────────────────────────────────

def _visual_score(visual_payload: dict) -> tuple[float, str]:
    """
    Scores the visual (YOLO) signal.

    Accepts:
        confidence      float   — model detection confidence [0,1]
        description     str     — description string (keywords searched)
        severity        int     — 1-5 (used as floor)
        detected_class  str     — raw YOLO class label (optional)
    Returns:
        (score: float, dominant_keyword: str)
    """
    conf  = float(visual_payload.get("confidence", 0.0))
    desc  = visual_payload.get("description", "").lower()
    sev   = int(visual_payload.get("severity", 1))
    label = visual_payload.get("detected_class", "").lower()

    # Start with model-reported confidence
    base = conf

    # Keyword boost from description or detected class
    keyword_score = 0.0
    dominant = ""
    for kw, kw_conf in KEYWORD_CONFIDENCE.items():
        if kw in desc or kw in label:
            if kw_conf > keyword_score:
                keyword_score = kw_conf
                dominant = kw

    # Severity floor: ensure score is at least the severity-mapped value
    severity_floor = SEVERITY_CONFIDENCE.get(sev, 0.2)

    score = max(base, keyword_score, severity_floor)
    return min(score, 1.0), dominant


def _acoustic_score(acoustic_payload: dict) -> tuple[float, str]:
    """
    Scores the acoustic (YAMNet + Whisper) signal.

    Accepts:
        confidence      float   — YAMNet top-class score [0,1]
        description     str     — description / class_name
        threat_type     str     — one of fire | medical | violence (optional)
        severity        int     — 1-5
    Returns:
        (score: float, dominant_keyword: str)
    """
    conf       = float(acoustic_payload.get("confidence", 0.0))
    desc       = acoustic_payload.get("description", "").lower()
    threat     = acoustic_payload.get("threat_type", "").lower()
    sev        = int(acoustic_payload.get("severity", 1))

    keyword_score = 0.0
    dominant = ""
    for kw, kw_conf in KEYWORD_CONFIDENCE.items():
        if kw in desc or kw in threat:
            if kw_conf > keyword_score:
                keyword_score = kw_conf
                dominant = kw

    severity_floor = SEVERITY_CONFIDENCE.get(sev, 0.2)
    score = max(conf, keyword_score, severity_floor)
    return min(score, 1.0), dominant


def _motion_score(motion_payload: dict) -> float:
    """
    Scores the motion signal derived from camera output.

    Accepts:
        person_count         int   — total people in frame
        fallen_detected      bool  — True if fallen-person aspect ratio triggered
        crowd_surge          bool  — True if person_count > threshold
        crowd_surge_count    int   — how many people triggered surge
        crowd_threshold      int   — config.CROWD_SURGE_THRESHOLD (default 10)
    Returns:
        score: float [0.0, 1.0]
    """
    person_count   = int(motion_payload.get("person_count", 0))
    fallen         = bool(motion_payload.get("fallen_detected", False))
    crowd_surge    = bool(motion_payload.get("crowd_surge", False))
    surge_count    = int(motion_payload.get("crowd_surge_count", 0))
    threshold      = int(motion_payload.get("crowd_threshold", 10))

    score = 0.0

    if fallen:
        score = max(score, 0.80)   # fallen person is a strong signal

    if crowd_surge:
        # Scale: at 2× threshold → 1.0
        ratio = surge_count / max(threshold * 2, 1)
        score = max(score, min(ratio, 1.0))

    # Mild signal just from presence of people (>0)
    if person_count > 0 and score == 0.0:
        score = min(person_count / 20.0, 0.30)   # 20 people → 0.30

    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────
#  Threat Classification
# ─────────────────────────────────────────────────────────────

def _classify_threat(description: str, threat_type: str = "") -> str:
    """Returns the canonical PanicZero threat type string."""
    desc = (description + " " + threat_type).lower()
    if any(w in desc for w in ["fire", "smoke", "flame", "blaze", "explosion"]):
        return "FIRE"
    if any(w in desc for w in ["medical", "fall", "fallen", "unconscious", "injury",
                                "help", "chest", "seizure", "collapse", "bleeding"]):
        return "MEDICAL"
    if any(w in desc for w in ["violence", "weapon", "knife", "gun", "fight",
                                "assault", "attack", "gunshot"]):
        return "VIOLENCE"
    if any(w in desc for w in ["crowd", "panic", "stampede", "evacuation"]):
        return "CROWD_CONTROL"
    return "GENERAL"


# ─────────────────────────────────────────────────────────────
#  Threat Level Label
# ─────────────────────────────────────────────────────────────

def _threat_level_label(score: float) -> str:
    for lo, hi, label in THREAT_LEVELS:
        if lo <= score < hi:
            return label
    return "CRITICAL"


# ─────────────────────────────────────────────────────────────
#  Main Fusion Function
# ─────────────────────────────────────────────────────────────

def fuse_signals(
    visual_payload:   Optional[dict] = None,
    acoustic_payload: Optional[dict] = None,
    motion_payload:   Optional[dict] = None,
    record_network_event: bool = True,
) -> dict:
    """
    Fuse all four sensor signals into a unified Guardian Mesh report.

    Parameters
    ----------
    visual_payload : dict | None
        Payload from YOLO camera model.
        Keys: confidence, description, severity, detected_class (optional)

    acoustic_payload : dict | None
        Payload from YAMNet / Whisper audio model.
        Keys: confidence, description, severity, threat_type (optional)

    motion_payload : dict | None
        Keys: person_count, fallen_detected, crowd_surge,
              crowd_surge_count, crowd_threshold

    record_network_event : bool
        If True, stamps the current time into the alert-frequency buffer.
        Set False when calling this purely for inspection without a new event.

    Returns
    -------
    dict with keys:
        confidence_score   float
        trigger_emergency  bool
        threat_level       str
        signal_breakdown   dict
        dominant_threat    str
        recommendation     str
        timestamp          str (ISO-8601 UTC)
    """

    # ── 1. Score each stream ──────────────────────────────────
    v_score, v_dominant = _visual_score(visual_payload or {})
    a_score, a_dominant = _acoustic_score(acoustic_payload or {})
    m_score              = _motion_score(motion_payload or {})
    n_score              = _network_signal_score()

    # Record event into the network-frequency buffer
    if record_network_event:
        record_alert_event()
        # Re-score network after recording
        n_score = _network_signal_score()

    # ── 2. Weighted fusion ────────────────────────────────────
    confidence_score = (
        WEIGHTS["visual"]   * v_score +
        WEIGHTS["acoustic"] * a_score +
        WEIGHTS["motion"]   * m_score +
        WEIGHTS["network"]  * n_score
    )
    confidence_score = round(min(max(confidence_score, 0.0), 1.0), 4)

    # ── 3. Dominant threat ────────────────────────────────────
    # Take description text from whichever payload is available
    combined_desc = " ".join([
        (visual_payload   or {}).get("description", ""),
        (acoustic_payload or {}).get("description", ""),
        (acoustic_payload or {}).get("threat_type", ""),
    ])
    dominant_keyword = v_dominant or a_dominant
    dominant_threat  = _classify_threat(combined_desc, dominant_keyword)

    # ── 4. Emergency gate ─────────────────────────────────────
    trigger_emergency = confidence_score >= EMERGENCY_THRESHOLD

    # ── 5. Build report ───────────────────────────────────────
    result = {
        "confidence_score":  confidence_score,
        "trigger_emergency": trigger_emergency,
        "threat_level":      _threat_level_label(confidence_score),
        "signal_breakdown": {
            "visual":   round(v_score, 4),
            "acoustic": round(a_score, 4),
            "motion":   round(m_score, 4),
            "network":  round(n_score, 4),
        },
        "dominant_threat":  dominant_threat,
        "recommendation":   RECOMMENDATIONS.get(dominant_threat, RECOMMENDATIONS["GENERAL"]),
        "timestamp":        datetime.utcnow().isoformat() + "Z",
    }

    _log_fusion_result(result)
    return result


# ─────────────────────────────────────────────────────────────
#  Convenience: fuse from a single crisis DetectionData dict
# ─────────────────────────────────────────────────────────────

def fuse_from_crisis(crisis_data: dict, source_type: str = "General System") -> dict:
    """
    Build a GuardianMesh report from a standard PanicZero crisis dict.
    This is called by process_crisis_alert() in main.py automatically.

    crisis_data keys used:
        description, severity, location, source
    """
    severity    = crisis_data.get("severity", 1)
    description = crisis_data.get("description", "")
    source      = (crisis_data.get("source", "") + " " + source_type).lower()

    # Route source into correct payload slot
    is_audio  = any(w in source for w in ["audio", "yamnet", "mic", "sound"])
    is_visual = any(w in source for w in ["camera", "yolo", "visual", "cv"])

    visual_payload   = None
    acoustic_payload = None

    common = {"description": description, "severity": severity, "confidence": 0.0}

    if is_audio:
        acoustic_payload = {**common, "threat_type": _classify_threat(description)}
    elif is_visual:
        visual_payload   = {**common, "detected_class": ""}
    else:
        # General — treat as both (lower weights)
        visual_payload   = {**common, "confidence": 0.0}
        acoustic_payload = {**common, "threat_type": _classify_threat(description)}

    return fuse_signals(
        visual_payload=visual_payload,
        acoustic_payload=acoustic_payload,
        motion_payload=None,
        record_network_event=True,
    )


# ─────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────

def _log_fusion_result(result: dict):
    bar_len   = 30
    score     = result["confidence_score"]
    filled    = int(score * bar_len)
    bar       = "█" * filled + "░" * (bar_len - filled)
    emergency = " EMERGENCY TRIGGERED" if result["trigger_emergency"] else " No emergency"

    print(
        f"\n[GUARDIAN MESH]   Confidence: {score:.2%}  [{bar}]  "
        f"{result['threat_level']} | {emergency}"
    )
    print(
        f"   Breakdown → "
        f"Visual:{result['signal_breakdown']['visual']:.2f}  "
        f"Acoustic:{result['signal_breakdown']['acoustic']:.2f}  "
        f"Motion:{result['signal_breakdown']['motion']:.2f}  "
        f"Network:{result['signal_breakdown']['network']:.2f}"
    )
    print(f"   Dominant threat : {result['dominant_threat']}")
    print(f"   Recommendation  : {result['recommendation']}")
