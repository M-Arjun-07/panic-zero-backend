import firebase_admin
from firebase_admin import credentials, firestore
import fakeredis.aioredis
import os
import json

# Initialize Firebase Admin SDK using the service account key
# We check if it's already initialized to prevent errors during hot-reloads in FastAPI
if not firebase_admin._apps:
    try:
        firebase_env = os.environ.get("FIREBASE_CONFIG_JSON")
        if firebase_env:
            parsed_dict = json.loads(firebase_env)
            cred = credentials.Certificate(parsed_dict)
            print("Using FIREBASE_CONFIG_JSON environment variable.")
        else:
            # Path to your downloaded service account key
            cred_path = os.path.join(os.path.dirname(__file__), "firebase-credentials.json")
            cred = credentials.Certificate(cred_path)
            print("Using local firebase-credentials.json file.")
            
        firebase_admin.initialize_app(cred)
        print("Firebase Admin SDK initialized successfully!")
    except Exception as e:
        print(f"Error initializing Firebase: {e}. Make sure FIREBASE_CONFIG_JSON is set or firebase-credentials.json is in the backend folder.")

# Get the Firestore database client
db = firestore.client()

# FakeRedis for local development of WebSockets without installing a Redis Server.
redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
