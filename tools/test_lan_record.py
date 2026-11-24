# -*- coding: utf-8 -*-
"""跨机记录验证：走局域网地址提交一次生成，检查 origin 与记录可见性"""
import json
import time
import urllib.request

BASE = "http://192.168.0.118:8787"
WF = "SDXL-场景底图-种草图.json"
PROMPT = "白色桌面上一台机箱，暖光，浅景深，产品照"


def post(path, payload, token):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-CW-Token": token}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"X-CW-Token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


tok = post("/api/login", {"username": "comfy", "password": "Comfy-5b51ea51"}, "")["token"]
res = post("/api/generate", {"workflow": WF, "prompt": PROMPT, "aspect": "16:9 (Widescreen)",
                             "megapixels": 2}, tok)
print("提交:", res)
pid = res.get("prompt_id")
for i in range(40):
    time.sleep(4)
    st = get("/api/genstatus?pid=" + pid, tok)
    it = st.get("item") or {}
    print(i, st.get("status"), "origin=", it.get("origin"),
          "files=", [f.get("filename") for f in st.get("files", [])], st.get("message"))
    if st.get("status") in ("done", "error"):
        break
