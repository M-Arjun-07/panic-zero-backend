"""
PanicZero — Rule Engine Quick Test
===================================
Run this WHILE the backend is running (python main.py).

Step 1: Adds a staff member to Firestore
Step 2: Triggers a crisis
Step 3: Shows you what the rule engine did

Usage:
    python test_rule_engine.py
"""

import httpx
import json

BASE = "http://127.0.0.1:8000"

def section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")

# ── STEP 1: Add a test staff member ──────────────
section("STEP 1: Adding test staff to Firestore")

staff_members = [
    {
        "name": "Arjun Sharma",
        "role": "Security",
        "medical_training": "Basic",
        "current_zone": "Main Hall",
        "latitude": 12.9716,
        "longitude": 77.5946,
        "status": "Available",
        "experience_years": 5
    },
    {
        "name": "Priya Nair",
        "role": "Medical",
        "medical_training": "Advanced",
        "current_zone": "Reception",
        "latitude": 12.9720,
        "longitude": 77.5950,
        "status": "Available",
        "experience_years": 8
    },
    {
        "name": "Rahul Verma",
        "role": "Fire Warden",
        "medical_training": "None",
        "current_zone": "Main Hall",
        "latitude": 12.9718,
        "longitude": 77.5948,
        "status": "Available",
        "experience_years": 3
    },
]

for s in staff_members:
    r = httpx.post(f"{BASE}/api/staff", json=s, timeout=10.0)
    if r.status_code == 200:
        print(f"   Added: {s['name']} ({s['role']}) in {s['current_zone']}")
    else:
        print(f"   Failed to add {s['name']}: {r.text}")

# ── STEP 2: Trigger a crisis ──────────────────────
section("STEP 2: Triggering a FIRE crisis (severity 4)")

crisis_payload = {
    "source": "Camera-01",
    "location": "Main Hall",
    "severity": 4,
    "description": "Smoke detected near electrical panel, possible fire"
}

r = httpx.post(f"{BASE}/api/crisis/detect", json=crisis_payload, timeout=10.0)
print(f"\n  HTTP Status: {r.status_code}")
result = r.json()
print(f"  Crisis ID  : {result['crisis']['id']}")
print(f"  Assigned To: {result['crisis'].get('assigned_staff', 'NONE — check console for error')}")
print(f"\n  Full Response:")
print(json.dumps(result, indent=4))

# ── STEP 3: Check active crises ───────────────────
section("STEP 3: Listing active crises")

r = httpx.get(f"{BASE}/api/crisis/active", timeout=10.0)
crises = r.json()["crises"]
print(f"\n  Active crises: {len(crises)}")
for c in crises:
    print(f"  → [{c['id']}] {c['description'][:40]} | Assigned: {c.get('assigned_staff', 'Unassigned')}")

# ── STEP 4: Check staff status ────────────────────
section("STEP 4: Checking staff status in Firestore")

r = httpx.get(f"{BASE}/api/staff", timeout=10.0)
all_staff = r.json()["staff"]
for s in all_staff:
    status_icon = " BUSY" if s.get("status") == "Busy" else " Available"
    print(f"  {status_icon} | {s['name']} ({s['role']}) | Zone: {s['current_zone']}")

# ── STEP 5: Test a medical crisis ─────────────────
section("STEP 5: Triggering a MEDICAL crisis (severity 5)")

crisis_payload2 = {
    "source": "Sensor-02",
    "location": "Reception",
    "severity": 5,
    "description": "Person unconscious and collapsed on floor"
}

r = httpx.post(f"{BASE}/api/crisis/detect", json=crisis_payload2, timeout=10.0)
result2 = r.json()
print(f"  Crisis ID  : {result2['crisis']['id']}")
print(f"  Assigned To: {result2['crisis'].get('assigned_staff', 'NONE')}")

# ── STEP 6: Reset for next test ───────────────────
section("STEP 6: Resetting all staff to Available")
r = httpx.post(f"{BASE}/api/staff/reset", timeout=20.0)
print(f"  {r.json()['message']}")

print(f"\n{'='*50}")
print("  TEST COMPLETE — Check server console for rule engine logs!")
print(f"{'='*50}\n")
