import json, time, base64, urllib.request, urllib.error

WF = "http://127.0.0.1:8787"
CU = "http://192.168.0.172:7177"
AUTH = "Basic " + base64.b64encode(b"comfy:Comfy-5b51ea51").decode()


def post(url, data, headers=None):
    req = urllib.request.Request(url, json.dumps(data).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:1500])
        raise


def get(url, headers=None):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


tok = post(WF + "/api/login", {"username": "comfy", "password": "Comfy-5b51ea51"})["token"]
pid = post(WF + "/api/generate", {
    "workflow": "MINIMax H3 文生视频.json",
    "prompt": "一只猫在窗台上打哈欠，阳光斜照，写实风格",
    "duration": 10,
}, {"X-CW-Token": tok})["prompt_id"]
print("submitted", pid)

q = None
for _ in range(10):
    time.sleep(0.3)
    q = get(CU + "/queue", {"Authorization": AUTH})
    ids = [x[1] for x in q.get("queue_pending", [])] + [x[1] for x in q.get("queue_running", [])]
    if pid in ids:
        break
print("in queue:", pid in ids)
for x in q.get("queue_pending", []) + q.get("queue_running", []):
    if x[1] == pid:
        val = x[2].get("21", {}).get("inputs", {}).get("value")
        print("node21 value =", val)

# 立刻清掉，别真的占用显卡跑十分钟
post(CU + "/queue", {"delete": [pid]}, {"Authorization": AUTH})
try:
    post(CU + "/interrupt", {}, {"Authorization": AUTH})
except Exception as e:  # noqa: BLE001
    print("interrupt:", e)
post(WF + "/api/delete", {"prompt_id": pid}, {"X-CW-Token": tok})
print("cleaned")
