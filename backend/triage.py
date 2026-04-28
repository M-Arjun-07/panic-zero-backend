"""
PanicZero — Dynamic Triage Engine (Swiggy Logic)
=================================================
Scores nearby hospitals like Swiggy scores restaurants:
  - Distance       (closer = better)
  - Traffic delay  (mock API — lower delay = better)
  - Bed availability (more free beds = better)
  - Specialist match (right doctor for the crisis type = massive bonus)

Returns the single best hospital and triggers mock ambulance dispatch.
"""

import math
import random
from datetime import datetime


# ─────────────────────────────────────────────
#  Mock Hospital Database
#  In production: fetch from a Hospital API / Firestore
# ─────────────────────────────────────────────

HOSPITALS = [
    {
        "id": "HOSP-001",
        "name": "City General Hospital",
        "latitude": 12.9722,
        "longitude": 77.5948,
        "total_beds": 200,
        "available_beds": 45,
        "specialists": ["Trauma", "Cardiology", "Burns", "General Surgery"],
        "trauma_center": True,
        "address": "12 MG Road, Bangalore",
    },
    {
        "id": "HOSP-002",
        "name": "St. Martha's Medical Centre",
        "latitude": 12.9750,
        "longitude": 77.6010,
        "total_beds": 150,
        "available_beds": 12,
        "specialists": ["Neurology", "Orthopedics", "General Surgery"],
        "trauma_center": False,
        "address": "45 Nrupathunga Rd, Bangalore",
    },
    {
        "id": "HOSP-003",
        "name": "Apollo Emergency Care",
        "latitude": 12.9680,
        "longitude": 77.5900,
        "total_beds": 300,
        "available_beds": 88,
        "specialists": ["Trauma", "Burns", "Cardiology", "Toxicology", "Pediatrics"],
        "trauma_center": True,
        "address": "Bannerghatta Road, Bangalore",
    },
    {
        "id": "HOSP-004",
        "name": "Manipal Hospital",
        "latitude": 12.9610,
        "longitude": 77.5780,
        "total_beds": 250,
        "available_beds": 0,   # Full — should be penalised heavily
        "specialists": ["Cardiology", "Oncology", "Neurology"],
        "trauma_center": False,
        "address": "98 HAL Airport Rd, Bangalore",
    },
    {
        "id": "HOSP-005",
        "name": "Victoria Government Hospital",
        "latitude": 12.9795,
        "longitude": 77.5720,
        "total_beds": 500,
        "available_beds": 120,
        "specialists": ["General Surgery", "Trauma", "Orthopedics", "Burns"],
        "trauma_center": True,
        "address": "Fort Rd, Bangalore",
    },
]


# ─────────────────────────────────────────────
#  Specialist Map: Crisis type → required specialist
# ─────────────────────────────────────────────

CRISIS_SPECIALIST_MAP = {
    "FIRE":          ["Burns", "Trauma", "General Surgery"],
    "MEDICAL":       ["Cardiology", "Trauma", "General Surgery", "Neurology"],
    "VIOLENCE":      ["Trauma", "General Surgery", "Orthopedics"],
    "CROWD_CONTROL": ["Trauma", "General Surgery", "Orthopedics"],
    "GENERAL":       ["General Surgery", "Trauma"],
}


# ─────────────────────────────────────────────
#  Utility: Haversine Distance (km)
# ─────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    R = 6371
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
#  Mock Traffic API
#  Returns estimated delay in minutes for a route.
#  In production: call Google Maps Distance Matrix API / HERE API
# ─────────────────────────────────────────────

def _get_traffic_delay_minutes(hospital_id: str, distance_km: float) -> float:
    """
    Mock traffic delay based on distance + random congestion factor.
    Real version: GET https://maps.googleapis.com/maps/api/distancematrix/json
    """
    # Base: assume 40 km/h average city speed
    base_time = (distance_km / 40) * 60  # minutes

    # Random congestion multiplier (1.0 = clear, 2.5 = heavy traffic)
    # Seeded by hospital_id for consistency within a single triage run
    seed = sum(ord(c) for c in hospital_id)
    rng = random.Random(seed)
    congestion = rng.uniform(1.0, 2.5)

    delay = base_time * congestion
    return round(delay, 1)


# ─────────────────────────────────────────────
#  Hospital Scoring Algorithm
# ─────────────────────────────────────────────

def _score_hospital(hospital: dict, incident_lat: float, incident_lon: float, threat_type: str) -> dict:
    """
    Scores a hospital. Lower score = better choice.

    Scoring factors:
      + distance_km * 10          (distance penalty)
      + traffic_delay_mins * 2    (traffic penalty)
      - available_beds * 0.5      (more beds = bonus)
      - specialist_match * 50     (right specialist = huge bonus)
      + 999 if no beds available  (disqualifying penalty)
    """
    score = 0.0
    breakdown = {}

    # 1. Distance
    dist_km = _haversine(incident_lat, incident_lon, hospital["latitude"], hospital["longitude"])
    dist_score = dist_km * 10
    score += dist_score
    breakdown["distance_km"] = round(dist_km, 2)
    breakdown["distance_score"] = round(dist_score, 1)

    # 2. Traffic delay (mock)
    traffic_mins = _get_traffic_delay_minutes(hospital["id"], dist_km)
    traffic_score = traffic_mins * 2
    score += traffic_score
    breakdown["traffic_delay_mins"] = traffic_mins
    breakdown["traffic_score"] = round(traffic_score, 1)

    # 3. Bed availability — disqualify if full
    available = hospital.get("available_beds", 0)
    if available == 0:
        score += 999  # Effectively disqualified
        breakdown["bed_penalty"] = 999
    else:
        bed_bonus = available * 0.5
        score -= bed_bonus
        breakdown["bed_bonus"] = round(bed_bonus, 1)
    breakdown["available_beds"] = available

    # 4. Specialist match
    required_specialists = CRISIS_SPECIALIST_MAP.get(threat_type, ["General Surgery"])
    hospital_specialists = hospital.get("specialists", [])
    matched = [s for s in required_specialists if s in hospital_specialists]
    if matched:
        specialist_bonus = len(matched) * 50
        score -= specialist_bonus
        breakdown["specialist_match"] = matched
        breakdown["specialist_bonus"] = specialist_bonus
    else:
        breakdown["specialist_match"] = []
        breakdown["specialist_bonus"] = 0

    # 5. Trauma center bonus for violent/severe crises
    if hospital.get("trauma_center") and threat_type in ["VIOLENCE", "FIRE", "CROWD_CONTROL"]:
        score -= 30
        breakdown["trauma_center_bonus"] = 30

    breakdown["final_score"] = round(score, 2)
    return breakdown


# ─────────────────────────────────────────────
#  Mock Ambulance Dispatch
# ─────────────────────────────────────────────

def _dispatch_ambulance(hospital: dict, incident_location: str, eta_mins: float) -> dict:
    """
    Mock ambulance dispatch.
    In production: call ambulance dispatch API / Twilio SMS to driver.
    """
    dispatch = {
        "dispatch_id": f"AMB-{abs(hash(hospital['id'] + incident_location)) % 100000:05d}",
        "hospital": hospital["name"],
        "hospital_address": hospital["address"],
        "incident_location": incident_location,
        "eta_minutes": round(eta_mins, 1),
        "dispatched_at": datetime.utcnow().isoformat(),
        "status": "DISPATCHED",
        "unit": f"Ambulance Unit #{random.randint(1, 20):02d}",
    }
    print(f"\n [AMBULANCE DISPATCH]")
    print(f"   Unit      : {dispatch['unit']}")
    print(f"   From      : {hospital['name']}")
    print(f"   To        : {incident_location}")
    print(f"   ETA       : {dispatch['eta_minutes']} minutes")
    print(f"   Dispatch ID: {dispatch['dispatch_id']}")
    print(f"   → [MOCK] In production this pings the ambulance driver app / SMS.")
    return dispatch


# ─────────────────────────────────────────────
#  Main Entry Point: Run Triage
# ─────────────────────────────────────────────

def run_triage(crisis_data: dict) -> dict:
    """
    Dynamic Triage Engine.

    Given a crisis, scores all hospitals and returns:
      - best_hospital: the selected hospital dict
      - score_breakdown: how each hospital was scored
      - ambulance_dispatch: the mock dispatch record
      - eta_minutes: time for ambulance to arrive

    Args:
        crisis_data: dict with keys:
            - location (str): incident zone name
            - severity (int): 1-5
            - threat_type (str): FIRE / MEDICAL / VIOLENCE / etc.
            - latitude (float, optional): incident GPS lat
            - longitude (float, optional): incident GPS lon
    """
    threat_type = crisis_data.get("threat_type", "GENERAL")
    incident_location = crisis_data.get("location", "Unknown Location")
    severity = crisis_data.get("severity", 3)

    # Use incident GPS if provided, else default to city centre
    incident_lat = crisis_data.get("latitude", 12.9716)
    incident_lon = crisis_data.get("longitude", 77.5946)

    print(f"\n[TRIAGE]  Running Dynamic Triage for {threat_type} at '{incident_location}' (severity {severity}/5)")

    # Score all hospitals
    scored = []
    for hospital in HOSPITALS:
        breakdown = _score_hospital(hospital, incident_lat, incident_lon, threat_type)
        scored.append({
            "hospital": hospital,
            "score": breakdown["final_score"],
            "breakdown": breakdown,
        })

    # Sort: lowest score = best
    scored.sort(key=lambda x: x["score"])

    # Print leaderboard
    print(f"\n  {'Hospital':<30} {'Score':>8}  {'Beds':>5}  {'ETA':>6}  Specialists")
    print(f"  {'-'*75}")
    for entry in scored:
        h = entry["hospital"]
        b = entry["breakdown"]
        eta = b.get("traffic_delay_mins", "?")
        specialists = ", ".join(b.get("specialist_match", [])) or "None matched"
        disq = "  FULL" if h.get("available_beds", 0) == 0 else ""
        print(f"  {h['name']:<30} {entry['score']:>8.1f}  {h['available_beds']:>5}  {eta:>5}m  {specialists}{disq}")

    best = scored[0]
    best_hospital = best["hospital"]
    eta_mins = best["breakdown"].get("traffic_delay_mins", 10.0)

    print(f"\n [TRIAGE RESULT] Best hospital: {best_hospital['name']} (score: {best['score']:.1f})")

    # Dispatch ambulance
    dispatch = _dispatch_ambulance(best_hospital, incident_location, eta_mins)

    return {
        "best_hospital": {
            "id": best_hospital["id"],
            "name": best_hospital["name"],
            "address": best_hospital["address"],
            "available_beds": best_hospital["available_beds"],
            "specialists": best_hospital["specialists"],
            "trauma_center": best_hospital["trauma_center"],
            "distance_km": best["breakdown"]["distance_km"],
            "eta_minutes": eta_mins,
            "score": best["score"],
        },
        "all_scores": [
            {
                "hospital_name": e["hospital"]["name"],
                "score": e["score"],
                "available_beds": e["hospital"]["available_beds"],
                "eta_minutes": e["breakdown"].get("traffic_delay_mins"),
                "specialist_match": e["breakdown"].get("specialist_match", []),
            }
            for e in scored
        ],
        "ambulance_dispatch": dispatch,
    }
