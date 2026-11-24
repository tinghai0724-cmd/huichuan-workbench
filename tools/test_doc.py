# -*- coding: utf-8 -*-
"""文档库链路自测：登录 -> 上传 -> 列表 -> 取正文"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8787"
USER, PASS = "comfy", "Comfy-5b51ea51"


def login():
    req = urllib.request.Request(BASE + "/api/login",
                                 data=json.dumps({"username": USER, "password": PASS}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def main():
    tok = login()
    path = sys.argv[1]
    name = os.path.basename(path)
    with open(path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        BASE + "/api/doc/upload?name=" + urllib.parse.quote(name), data=body, method="POST",
        headers={"Content-Type": "application/octet-stream", "X-CW-Token": tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode())
    print("上传:", json.dumps(res.get("item"), ensure_ascii=False)[:400])

    req = urllib.request.Request(BASE + "/api/docs", headers={"X-CW-Token": tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        lst = json.loads(r.read().decode())
    print("文档数:", len(lst.get("items", [])))

    did = res["item"]["id"]
    req = urllib.request.Request(BASE + "/api/doc?id=" + did, headers={"X-CW-Token": tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        doc = json.loads(r.read().decode())
    for s in doc.get("sheets", []):
        print(f"  {s['name']}: {len(s['rows'])} 行")
        for row in s["rows"][:3]:
            print("    ", " | ".join(x[:40] for x in row))


if __name__ == "__main__":
    main()
