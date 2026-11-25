import json, urllib.request, sys

BASE = "http://127.0.0.1:8787"

def call(path, data=None, token=None):
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("X-CW-Token", token)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
        req.method = "POST"
    with urllib.request.urlopen(req, body, timeout=60) as r:
        return json.loads(r.read().decode())

tok = call("/api/login", {"username": "comfy", "password": "Comfy-5b51ea51"})["token"]
st = call("/api/state", token=tok)
print("ratios:", st["ratios"])
print("video_duration:", st.get("video_duration"))
print("video_workflows:", st.get("video_workflows"))
for w in st["workflows"]:
    if w.get("kind") == "视频" or w.get("video"):
        print("  ", w["name"], "ok=", w["ok"], "can_duration=", w.get("can_duration"),
              "default=", w.get("duration_default"), "slots=", w.get("image_slots"),
              "size_mode=", w.get("size_mode"))
