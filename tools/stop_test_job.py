import json, base64, urllib.request

CU = "http://192.168.0.172:7177"
AUTH = "Basic " + base64.b64encode(b"comfy:Comfy-5b51ea51").decode()
PID = "7dc1501e-624b-4492-8323-a0e76da68305"


def req(url, data=None, method="GET"):
    r = urllib.request.Request(url, (json.dumps(data).encode() if data is not None else None), method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", AUTH)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, resp.read().decode()[:200]


print("queue before:", req(CU + "/queue"))
print("interrupt:", req(CU + "/interrupt", {}, "POST"))
print("delete:", req(CU + "/queue", {"delete": [PID]}, "POST"))
print("queue after:", req(CU + "/queue"))
