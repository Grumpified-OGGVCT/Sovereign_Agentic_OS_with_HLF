import json
import os
import sys
import time
import redis

try:
    import ulid
    def get_ulid():
        return str(ulid.new())
except ImportError:
    import uuid
    def get_ulid():
        return str(uuid.uuid4())

def dispatch(intent: str):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = redis.from_url(redis_url, decode_responses=True)
        request_id = get_ulid()
        payload = {
            "request_id": request_id,
            "text": intent,
            "timestamp": time.time(),
            "ast": {"version": "0.3.0", "program": [], "text_mode": True}
        }
        
        # Publish to the 'intents' stream
        r.xadd("intents", {"data": json.dumps(payload)})
        print(f"Successfully dispatched intent '{intent}' with ID: {request_id}")
    except Exception as e:
        print(f"Error dispatching intent: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dispatch.py <intent>")
        sys.exit(1)
    
    dispatch(sys.argv[1])
