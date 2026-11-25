import json, time, base64, urllib.request, uuid, os

WF = "http://127.0.0.1:8787"
CU = "http://192.168.0.172:7177"
AUTH = "Basic " + base64.b64encode(b"comfy:Comfy-5b51ea51").decode()
IMG = r"C:\Users\Administrator\AppData\Local\Doubao\User Data\ClipboardTemp\6863ad85-7304-46fa-915b-5c6d38fa9f5e.png"


def post(url, data=None, headers=None, raw=None, ctype=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else b"")
    req = urllib.request.Request(url, body, method="POST")
    if ctype:
        req.add_header("Content-Type", ctype)
    elif data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip().startswith(("{", "[")) else txt
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:1200])
        raise


def get(url, headers=None):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


tok = post(WF + "/api/login", {"username": "comfy", "password": "Comfy-5b51ea51"})["token"]

# 1) 上传一张参考图
boundary = "----cw" + uuid.uuid4().hex
with open(IMG, "rb") as f:
    img = f.read()
parts = [
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"cw_verify.png\"\r\nContent-Type: image/png\r\n\r\n".encode(),
    img,
    f"\r\n--{boundary}--\r\n".encode(),
]
up = post(WF + "/api/upload", raw=b"".join(parts), ctype="multipart/form-data; boundary=" + boundary,
          headers={"X-CW-Token": tok})
print("upload:", up)

# 2) 提交一条 图生视频（5 秒，1.0MP 16:9）
r = post(WF + "/api/generate", {
    "workflow": "minimax h3 图生视频.json",
    "prompt": "镜头缓慢推进，画面轻微呼吸感，光影柔和",
    "images": [up["name"]],
    "aspect": "16:9 (Widescreen)",
    "megapixels": 1.0,
    "duration": 5,
}, {"X-CW-Token": tok})
print("generate:", r)
pid = r["prompt_id"]

# 3) 等 25 秒，看是否越过了原来报错的 Attention 节点；然后立刻清掉这条任务
time.sleep(25)
q = get(CU + "/queue", {"Authorization": AUTH})
hist = get(CU + "/history?max_items=2", {"Authorization": AUTH})
state, ran = None, []
if pid in hist:
    st = hist[pid].get("status", {})
    state = st.get("status_str")
    for m in st.get("messages", []):
        if m[0] == "execution_error":
            print("ERROR:", str(m[1])[:500])
        if m[0] == "execution_start":
            ran.append("start")
print("queue:", len(q["queue_running"]), len(q["queue_pending"]), "| history:", state,
      "| ours in queue:", any(x[1] == pid for x in q["queue_running"] + q["queue_pending"]))
for x in q["queue_running"]:
    if x[1] == pid:
        print("running ours, interrupting")
        post(CU + "/interrupt", {}, {"Authorization": AUTH})
post(CU + "/queue", {"delete": [pid]}, {"Authorization": AUTH})
time.sleep(1)
post(WF + "/api/delete", {"prompt_id": pid}, {"X-CW-Token": tok})
print("cleaned; queue now:", len(get(CU + "/queue", {"Authorization": AUTH})["queue_running"]))
