"""多参考图链路实测：上传两张图 -> /api/generate 带 images -> 轮询结果"""
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8787"
USER, PASS = "comfy", "Comfy-5b51ea51"
WORKFLOW = "产品场景复刻-Qwen2511.json"


def post_json(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("X-CW-Token", token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get_json(path, token):
    req = urllib.request.Request(BASE + path, headers={"X-CW-Token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def upload(path, token):
    boundary = "----cw" + uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    parts = []
    for field, value in (("type", "input"), ("overwrite", "true")):
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                      f'{value}\r\n').encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                  f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n').encode())
    parts.append(data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        BASE + "/api/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-CW-Token": token, "Content-Length": str(len(body))})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def main():
    tok = post_json("/api/login", {"username": USER, "password": PASS})["token"]
    files = [p for p in sys.argv[1:] if os.path.exists(p)]
    if len(files) < 2:
        print("需要两张本地图片")
        return
    names = []
    for p in files[:5]:
        res = upload(p, tok)
        names.append(res.get("name"))
        print("上传:", os.path.basename(p), "->", res.get("name"))
    r = post_json("/api/generate", {
        "workflow": WORKFLOW, "prompt": "把两张产品图里的产品放到同一个桌面场景",
        "images": names, "aspect": "16:9 (Widescreen)", "megapixels": 2,
    }, tok)
    print("提交:", r)
    pid = r.get("prompt_id")
    if not pid:
        return
    for i in range(30):
        time.sleep(4)
        st = get_json("/api/item?id=" + pid, tok)
        print(i, st.get("status"), [f.get("filename") for f in st.get("files", [])],
              st.get("error"))
        if st.get("status") in ("done", "error"):
            break


if __name__ == "__main__":
    main()
