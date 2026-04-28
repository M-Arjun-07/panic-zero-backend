from pydantic import BaseModel
from typing import Optional

class Staff(BaseModel):
    """
    Staff model for validating data before inserting into Firestore.
    """
    name: str
    role: str                               # e.g. "Security", "Medical", "Fire Warden", "Manager"
    medical_training: str = "None"          # "None" | "Basic" | "Advanced"
    current_zone: str                       # Physical zone/room name
    latitude: Optional[float] = None        # GPS latitude (optional)
    longitude: Optional[float] = None       # GPS longitude (optional)
    status: str = "Available"              # "Available" | "Busy" | "Off Duty"
    fcm_token: Optional[str] = None         # Firebase Cloud Messaging device token
    experience_years: Optional[int] = 0    # Used as tiebreaker in scoring
