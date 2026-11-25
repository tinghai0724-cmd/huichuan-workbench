"""实测视频流程：上传参考图 -> 提交 -> 轮询到出片 -> 验证 Range 取流

用法： python tools\\test_video.py "工作流名" 图1 [图2]
"""
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid

API = "http://127.0.0.1:8787"
WF = sys.argv[1] if len(sys.argv) > 1 else "minimax H3 首尾帧.json"
IMGS = sys.argv[2:] or [r"C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\多图实测-输出.png"]
PROMPT = "黄昏城市街头，女孩回头微笑，镜头缓慢推进，电影感"


def req(path, data=None, headers=None):
    h = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(API + path, data=body, headers=h)
    with urllib.request.urlopen(r, timeout=120) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, (json.loads(raw) if "json" in ctype else raw), resp.headers


def multipart(fn, raw, boundary):
    head = [
        "--" + boundary,
        'Content-Disposition: form-data; name="image"; filename="%s"' % fn,
        "Content-Type: image/png",
        "",
    ]
    tail = [
        "--" + boundary,
        'Content-Disposition: form-data; name="type"',
        "",
        "input",
        "--" + boundary,
        'Content-Disposition: form-data; name="overwrite"',
        "",
        "true",
        "--" + boundary + "--",
        "",
    ]
    body = b"".join(p.encode("utf-8") + b"\r\n" for p in head) + raw + b"\r\n"
    return body + b"".join(p.encode("utf-8") + b"\r\n" for p in tail)


st, j, _ = req("/api/login", {"username": "comfy", "password": "Comfy-5b51ea51"})
TOK = {"X-CW-Token": j["token"]}
print("login ok", st)

names = []
for p in IMGS:
    raw = open(p, "rb").read()
    fn = "vt_%s_%s" % (uuid.uuid4().hex[:8], p.split("\\")[-1].replace(" ", "_"))
    boundary = "----cw" + uuid.uuid4().hex
    r = urllib.request.Request(API + "/api/upload", data=multipart(fn, raw, boundary),
                               headers={**TOK,
                                        "Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(r, timeout=120) as resp:
        up = json.loads(resp.read())
    names.append(up["name"])
    print("uploaded", up["name"])

st, j, _ = req("/api/generate", {"workflow": WF, "prompt": PROMPT, "images": names,
                                 "image": names[0], "aspect": "16:9 (Widescreen)",
                                 "megapixels": 2}, TOK)
print("submit:", st, json.dumps(j, ensure_ascii=False))
pid = j.get("prompt_id")
if not pid:
    sys.exit(1)

start = time.time()
last = None
while time.time() - start < 480:
    time.sleep(6)
    try:
        st, s, _ = req("/api/genstatus?pid=" + pid, headers=TOK)
    except Exception as e:                      # noqa: BLE001
        print("status err", e)
        continue
    line = "%ss 状态=%s 已用=%s %s" % (int(time.time() - start), s.get("status"),
                                       s.get("elapsed"), (s.get("message") or "")[:120])
    if line != last:
        print(line)
        last = line
    if s.get("status") in ("done", "error"):
        print("final:", json.dumps({k: s.get(k) for k in ("status", "files", "message")},
                                   ensure_ascii=False)[:700])
        files = s.get("files") or []
        if files:
            f = files[0]
            q = urllib.parse.urlencode({"filename": f["filename"],
                                        "subfolder": f.get("subfolder", ""),
                                        "type": f.get("type", "output"),
                                        "t": TOK["X-CW-Token"]})
            r = urllib.request.Request(API + "/api/img?" + q, headers={"Range": "bytes=0-1023"})
            with urllib.request.urlopen(r, timeout=60) as resp:
                print("range:", resp.status, resp.headers.get("Content-Range"),
                      "len=" + str(len(resp.read())))
        break
