import math
import httpx
import os
from database import db
from datetime import datetime

# ─────────────────────────────────────────────
#  FCM Configuration
#  Set PANIC_FCM_SERVER_KEY in your environment
#  to enable real push notifications.
#  Without it, the engine falls back to mock logs.
# ─────────────────────────────────────────────
FCM_SERVER_KEY = os.environ.get("PANIC_FCM_SERVER_KEY", "")
FCM_URL = "https://fcm.googleapis.com/fcm/send"


# ─────────────────────────────────────────────
#  Utility: Haversine Distance (km)
# ─────────────────────────────────────────────
def calculate_distance(lat1, lon1, lat2, lon2) -> float:
    """
    Haversine formula — returns real-world km distance between two GPS coords.
    Falls back to infinity if any coord is missing.
    """
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    R = 6371  # Earth radius in km
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─────────────────────────────────────────────
#  Threat Matrix: Role & Protocol Determination
# ─────────────────────────────────────────────

# Maps keywords in crisis description → response protocol.
# Each entry: (keyword_list, required_roles, team_size_per_severity_level, needs_medical, escalate_threshold)
THREAT_MATRIX = [
    {
        "keywords": ["fire", "smoke", "flame", "blaze"],
        "required_roles": ["Security", "Fire Warden"],
        "team_size": {1: 1, 2: 1, 3: 2, 4: 3, 5: 4},
        "required_medical": None,
        "escalate_threshold": 3,          # Severity >= 3 → call fire dept
    },
    {
        "keywords": ["medical", "fall", "unconscious", "collapsed", "injury", "help", "chest pain", "seizure"],
        "required_roles": ["Medical", "First Responder", "Nurse", "Doctor"],
        "team_size": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        "required_medical": "Basic",       # severity >= 4 upgrades to "Advanced"
        "escalate_threshold": 4,           # Severity >= 4 → call ambulance
    },
    {
        "keywords": ["violence", "weapon", "knife", "gun", "fight", "assault", "threat", "attack"],
        "required_roles": ["Security"],
        "team_size": {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        "required_medical": None,
        "escalate_threshold": 2,           # Any weapon → call police
    },
    {
        "keywords": ["crowd", "panic", "stampede", "evacuation"],
        "required_roles": ["Security", "Manager", "Fire Warden"],
        "team_size": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
        "required_medical": None,
        "escalate_threshold": 3,
    },
]

# Default fallback if no keyword matches
DEFAULT_PROTOCOL = {
    "required_roles": ["Security", "Manager", "General"],
    "team_size": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    "required_medical": None,
    "escalate_threshold": 5,  # Only escalate on maximum severity
}


def determine_response_protocol(crisis_data: dict) -> dict:
    """
    Scans the crisis description against the Threat Matrix and returns a
    resolved protocol dict with role requirements, team size, and escalation flag.
    """
    description = crisis_data.get("description", "").lower()
    severity = max(1, min(5, crisis_data.get("severity", 3)))  # Clamp 1-5

    matched_rule = None
    for rule in THREAT_MATRIX:
        if any(kw in description for kw in rule["keywords"]):
            matched_rule = rule
            break

    rule = matched_rule or DEFAULT_PROTOCOL

    # Upgrade medical requirement for high-severity medical crises
    required_medical = rule["required_medical"]
    if required_medical == "Basic" and severity >= 4:
        required_medical = "Advanced"

    protocol = {
        "required_roles": rule["required_roles"],
        "required_medical": required_medical,
        "team_size": rule["team_size"].get(severity, 2),
        "escalate_to_authorities": severity >= rule["escalate_threshold"],
        "threat_type": _classify_threat(description),
    }

    print(f"\n[RULE ENGINE]  Protocol resolved for severity={severity}: {protocol}")
    return protocol


def _classify_threat(description: str) -> str:
    """Returns a human-readable threat category label."""
    desc = description.lower()
    if any(w in desc for w in ["fire", "smoke", "flame", "blaze"]):
        return "FIRE"
    if any(w in desc for w in ["medical", "fall", "unconscious", "injury", "help", "chest", "seizure"]):
        return "MEDICAL"
    if any(w in desc for w in ["violence", "weapon", "knife", "gun", "fight", "assault"]):
        return "VIOLENCE"
    if any(w in desc for w in ["crowd", "panic", "stampede", "evacuation"]):
        return "CROWD_CONTROL"
    return "GENERAL"


# ─────────────────────────────────────────────
#  FCM Push Notification (Real + Mock Fallback)
# ─────────────────────────────────────────────

async def send_fcm_notification(staff: dict, crisis_data: dict, protocol: dict):
    """
    Sends a real FCM push notification to the staff member's device token.
    If no FCM server key is configured, logs a realistic mock payload instead.
    """
    severity = crisis_data.get("severity", 1)
    location = crisis_data.get("location", "Unknown Location")
    description = crisis_data.get("description", "Crisis detected")
    threat_type = protocol.get("threat_type", "GENERAL")

    title = f" [{threat_type}] Emergency Response Required"
    body = (
        f"ROLE: {staff.get('role', 'Responder')} | "
        f"ZONE: {location} | "
        f"SEVERITY: {severity}/5 | "
        f"DETAILS: {description[:80]}"
    )

    payload = {
        "priority": "high",
        "notification": {
            "title": title,
            "body": body,
            "sound": "emergency_alert",
            "android_channel_id": "panic_zero_alerts",
        },
        "data": {
            "crisis_id": crisis_data.get("id", ""),
            "location": location,
            "severity": str(severity),
            "threat_type": threat_type,
            "role": staff.get("role", ""),
            "click_action": "OPEN_DASHBOARD",
        },
    }

    fcm_token = staff.get("fcm_token", "")

    if FCM_SERVER_KEY and fcm_token:
        # ── Real FCM Call ──
        payload["to"] = fcm_token
        headers = {
            "Authorization": f"key={FCM_SERVER_KEY}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(FCM_URL, headers=headers, json=payload)
                if resp.status_code == 200:
                    print(f"   [FCM ] Notification sent to {staff['name']} (token: ...{fcm_token[-6:]})")
                else:
                    print(f"   [FCM ] Failed for {staff['name']}: {resp.status_code} {resp.text[:120]}")
        except Exception as e:
            print(f"   [FCM ] Network error for {staff['name']}: {e}")
    else:
        # ── Mock FCM Log ──
        mode = "NO FCM KEY" if not FCM_SERVER_KEY else "NO TOKEN"
        print(f"   [FCM MOCK — {mode}] → {staff.get('name', 'Unknown')}")
        print(f"       Title : {title}")
        print(f"       Body  : {body}")


# ─────────────────────────────────────────────
#  Main Entry Point: Assign Staff to Crisis
# ─────────────────────────────────────────────

async def assign_staff_to_crisis(crisis_data: dict) -> dict | None:
    """
    The core Rule Engine.

    Steps:
      1. Resolve protocol from Threat Matrix
      2. Query Firestore for all available staff
      3. Filter by role + medical training requirement
      4. Score staff (zone match + GPS distance)
      5. Select best team based on team_size
      6. Send FCM push to each team member
      7. Mark assigned staff as 'Busy' in Firestore
      8. Escalate to authorities if threshold met
      9. Return the primary responder dict
    """
    protocol = determine_response_protocol(crisis_data)

    crisis_lat = crisis_data.get("latitude", None)
    crisis_lon = crisis_data.get("longitude", None)
    crisis_zone = crisis_data.get("location", "Unknown")

    try:
        staff_docs = db.collection("staff").where("status", "==", "Available").stream()
        available_staff = []

        for doc in staff_docs:
            staff = doc.to_dict()
            staff["id"] = doc.id

            # ── Role Filter ──
            role_match = staff.get("role") in protocol["required_roles"]
            if not role_match:
                continue

            # ── Medical Training Filter ──
            if protocol["required_medical"]:
                staff_med = staff.get("medical_training", "None")
                # "Advanced" satisfies both "Basic" and "Advanced" requirements
                med_levels = {"None": 0, "Basic": 1, "Advanced": 2}
                required_level = med_levels.get(protocol["required_medical"], 0)
                staff_level = med_levels.get(staff_med, 0)
                if staff_level < required_level:
                    continue

            available_staff.append(staff)

        # ── Scoring: lower = better ──
        def score_staff(staff: dict) -> float:
            score = 0.0

            # Zone bonus: same physical zone gets massive priority
            if staff.get("current_zone", "").lower() == crisis_zone.lower():
                score -= 100.0

            # GPS distance (Haversine)
            if crisis_lat is not None and crisis_lon is not None:
                dist_km = calculate_distance(
                    crisis_lat, crisis_lon,
                    staff.get("latitude"), staff.get("longitude")
                )
                if dist_km != float("inf"):
                    score += dist_km

            # Tiebreaker: prefer staff with higher experience (if field exists)
            score -= staff.get("experience_years", 0) * 0.5

            return score

        available_staff.sort(key=score_staff)
        assigned_team = available_staff[: protocol["team_size"]]

        if not assigned_team:
            print("\n [RULE ENGINE] No available staff match protocol requirements!")
            _trigger_all_staff_alarm(crisis_zone, protocol)
            if protocol["escalate_to_authorities"]:
                _escalate_to_authorities(crisis_zone, protocol["threat_type"], crisis_data.get("severity", 5))
            return None

        # ── Execute Protocol ──
        team_names = [s["name"] for s in assigned_team]
        print(f"\n [RULE ENGINE] Team deployed → {crisis_zone}: {', '.join(team_names)}")

        for staff in assigned_team:
            # Send push notification (real or mock)
            await send_fcm_notification(staff, crisis_data, protocol)

            # Mark staff as Busy in Firestore
            try:
                db.collection("staff").document(staff["id"]).update({
                    "status": "Busy",
                    "assigned_crisis": crisis_data.get("id", ""),
                    "assigned_at": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                print(f"    Could not update status for {staff['name']}: {e}")

        # ── External Escalation ──
        if protocol["escalate_to_authorities"]:
            _escalate_to_authorities(crisis_zone, protocol["threat_type"], crisis_data.get("severity", 3))

        # Return primary responder
        return assigned_team[0]

    except Exception as e:
        print(f"\n [RULE ENGINE ERROR] Engine failure: {e}\n")
        return None


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _trigger_all_staff_alarm(zone: str, protocol: dict):
    """Triggered when no staff are available. Would broadcast a general alarm."""
    print(f"\n [ALL-STAFF ALARM] No {protocol['required_roles']} available for {zone}!")
    print("   → Broadcasting general emergency alarm to all staff on duty.")


def _escalate_to_authorities(zone: str, threat_type: str, severity: int):
    """
    Mock external escalation to emergency services.
    In production: call 911 API / send SMS via Twilio / etc.
    """
    service_map = {
        "FIRE": " Fire Department",
        "MEDICAL": " Ambulance / EMS",
        "VIOLENCE": " Police / Armed Response",
        "CROWD_CONTROL": " Police +  Fire Department",
        "GENERAL": " Emergency Services",
    }
    service = service_map.get(threat_type, " Emergency Services")
    print(f"\n [ESCALATION] Severity {severity}/5 — Alerting {service} for incident at '{zone}'")
    print(f"   → [MOCK] In production this calls Twilio / 911 API / SMS gateway.")
