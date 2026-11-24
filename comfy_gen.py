#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI API 自动出图脚本
------------------------------------------------
把 ComfyUI 前端保存的「工作流 JSON」自动转成 API 格式（/api/prompt 需要的格式），
提交任务、轮询进度、下载成图。只依赖 Python 标准库。

基本用法：
    python comfy_gen.py --workflow "KOOK-Minimax-H3-image-尘星.json" \
                        --prompt "你的提示词" \
                        --image "参考图.png" \
                        --megapixels 4 --aspect "16:9" \
                        --output ./out

常用参数：
    --dry-run        只打印转换后的 API JSON，不提交（不占用 GPU）
    --width/--height 直接指定出图像素（覆盖 ResolutionSelector 的比例/像素设置）
    --seed           固定种子；--random-seed 随机
    --steps --cfg --denoise --lora-strength
    --save-api       把转换结果存成 api_*.json 方便以后直接复用

凭据也可以用环境变量：COMFY_USER / COMFY_PASS
"""

import argparse
import base64
import json
import mimetypes
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

NOTE_TYPES = {"MarkdownNote", "Note", "Notes"}
# 纯前端/纯路由节点：不参与执行，直接跳过（连线穿过去）
UI_ONLY_TYPES = {
    "Reroute", "PrimitiveNode", "Note", "MarkdownNote", "Notes",
    "Label (rgthree)", "Fast Groups Muter (rgthree)", "Fast Groups Bypasser (rgthree)",
    "Bookmark (rgthree)", "Fast Bypasser (rgthree)", "Mute (rgthree)",
    "Node Collector (rgthree)", "Power Lora Loader (rgthree)",
}


def is_ui_only(ntype):
    return ntype in UI_ONLY_TYPES or "(rgthree)" in ntype


def _expand_subgraphs(ui):
    """把新版 ComfyUI 的子图节点（type 是 uuid）就地展开成扁平图"""
    defs = {sg.get("id"): sg for sg in ((ui.get("definitions") or {}).get("subgraphs") or [])}
    if not defs:
        return ui

    nodes = [dict(n) for n in ui.get("nodes") or []]
    links = [list(l) for l in (ui.get("links") or [])]

    for _ in range(6):
        pending = [n for n in nodes if n.get("type") in defs]
        if not pending:
            break
        link_by_id = {l[0]: l for l in links}
        out_nodes, out_links = [], list(links)

        for n in nodes:
            sg = defs.get(n.get("type"))
            if not sg:
                out_nodes.append(n)
                continue

            prefix = f"{n['id']}:"
            inner_nodes = [{**x, "id": prefix + str(x["id"]),
                            "inputs": [dict(s) for s in (x.get("inputs") or [])],
                            "outputs": [dict(s) for s in (x.get("outputs") or [])]}
                           for x in sg.get("nodes") or []]
            remap = {}

            for li in sg.get("links") or []:
                lid, oid, oslot, tid, tslot, ltype = li
                if oid == -10:                              # 子图输入 → 上游节点
                    slot = [s for s in (n.get("inputs") or []) if s.get("link") is not None]
                    up_id = n["inputs"][oslot]["link"] if oslot < len(n.get("inputs") or []) else None
                    up = link_by_id.get(up_id)
                    if not up:
                        continue
                    nid = f"{prefix}in{lid}"
                    out_links.append([nid, up[1], up[2], prefix + str(tid), tslot, ltype])
                    remap[lid] = nid
                elif tid == -20:                            # 子图输出 → 下游节点
                    for pl in links:
                        if pl[1] == n["id"] and pl[2] == tslot:
                            nid = f"{prefix}out{lid}_{pl[0]}"
                            out_links.append([nid, prefix + str(oid), oslot, pl[3], pl[4], ltype])
                else:                                       # 子图内部连线
                    nid = f"{prefix}l{lid}"
                    out_links.append([nid, prefix + str(oid), oslot, prefix + str(tid), tslot, ltype])
                    remap[lid] = nid

            for node in inner_nodes:
                for sock in node["inputs"]:
                    if sock.get("link") is not None and sock["link"] in remap:
                        sock["link"] = remap[sock["link"]]
            out_nodes.extend(inner_nodes)

        # 丢掉指向/来自子图节点本身的旧连线
        sub_ids = {n["id"] for n in pending}
        out_links = [l for l in out_links
                     if not (l[1] in sub_ids or l[3] in sub_ids)]
        nodes, links = out_nodes, out_links

    return {"nodes": nodes, "links": links}


# 属于「连线」而非「控件」的类型名
LINK_TYPE_KEYWORDS = {
    "MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "MASK", "AUDIO",
    "VIDEO", "SEG", "SEGS", "NOISE", "SAMPLER", "SIGMAS", "GUIDER", "CONTROL_NET",
    "CLIP_VISION", "STYLE_MODEL", "GLIGEN", "UPSCALE_MODEL", "COMFY_AUTOGROW_V3",
    "LATENT_IMAGE", "WEBCAM", "TUPLE", "PIPE", "RESOLUTION_PREVIEW",
}


class ComfyError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:800]}")


class Comfy:
    def __init__(self, base, user=None, password=None, timeout=180):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.auth = None
        if user:
            token = base64.b64encode(f"{user}:{password or ''}".encode("utf-8")).decode("ascii")
            self.auth = "Basic " + token

    # ---------- 底层请求 ----------
    def _open(self, req):
        if self.auth:
            req.add_header("Authorization", self.auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return json.loads(body.decode("utf-8"))
                return body
        except urllib.error.HTTPError as e:
            raise ComfyError(e.code, e.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise ComfyError(0, f"连接失败: {e.reason}")

    def get(self, path):
        return self._open(urllib.request.Request(self.base + path, method="GET"))

    def post_json(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        return self._open(req)

    # ---------- 业务接口 ----------
    def system_stats(self):
        return self.get("/api/system_stats")

    def object_info(self):
        return self.get("/api/object_info")

    def queue_info(self):
        return self.get("/api/queue")

    def upload_image(self, file_path, subfolder="", overwrite=True):
        """上传图片到 input 目录，返回文件名（可直接填进 LoadImage 节点）"""
        boundary = "----comfy" + uuid.uuid4().hex
        parts = []

        def field(name, value):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )

        field("type", "input")
        field("subfolder", subfolder)
        field("overwrite", "true" if overwrite else "false")

        filename = os.path.basename(file_path)
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode()
        )
        with open(file_path, "rb") as f:
            parts.append(f.read())
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        body = b"".join(parts)
        req = urllib.request.Request(
            self.base + "/api/upload/image", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        res = self._open(req)
        name = res.get("name", filename)
        sub = res.get("subfolder", subfolder)
        return f"{sub}/{name}" if sub else name

    def submit(self, prompt, client_id=None, extra_data=None):
        payload = {"prompt": prompt, "client_id": client_id or uuid.uuid4().hex}
        if extra_data:
            payload["extra_data"] = extra_data
        return self.post_json("/api/prompt", payload)

    def wait(self, prompt_id, timeout=1200, interval=2, verbose=True):
        start = time.time()
        last_note = 0
        while True:
            hist = self.get(f"/api/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise ComfyError(200, "执行失败: " + json.dumps(msgs, ensure_ascii=False))
                return entry
            if time.time() - start > timeout:
                raise TimeoutError(f"等待超时（{timeout}s），prompt_id={prompt_id} 仍在队列中")
            if verbose and int(time.time() - start) // 30 != last_note:
                last_note = int(time.time() - start) // 30
                q = self.queue_info()
                print(f"[{int(time.time()-start)}s] 运行中 {len(q.get('queue_running', []))} / "
                      f"排队 {len(q.get('queue_pending', []))}", flush=True)
            time.sleep(interval)

    def download_outputs(self, entry, out_dir):
        """把 history 里的输出图片下载到本地，返回文件列表"""
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        for node_out in (entry.get("outputs") or {}).values():
            for key in ("images", "gifs", "videos"):
                for item in node_out.get(key, []) or []:
                    if item.get("type") not in (None, "output"):
                        continue
                    params = urllib.parse.urlencode({
                        "filename": item.get("filename", ""),
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    })
                    data = self.get("/api/view?" + params)
                    path = os.path.join(out_dir, item.get("filename"))
                    with open(path, "wb") as f:
                        f.write(data)
                    saved.append(path)
        return saved


# ---------- 工作流转换 ----------
def is_widget_spec(spec):
    """判断某个输入是不是控件（有值可写在 prompt 里），还是纯连线插口"""
    if not isinstance(spec, list) or not spec:
        return False
    head = spec[0]
    if isinstance(head, list):              # 新版 COMBO：选项写成嵌套列表
        return True
    if not isinstance(head, str):
        return False
    if head in ("INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", "RESOLUTION_PREVIEW"):
        return True
    if head.upper() in {k.upper() for k in LINK_TYPE_KEYWORDS}:
        return False
    return True                             # 旧版 COMBO：["选项1","选项2",...]


def convert_ui_to_api(ui, object_info):
    """UI 工作流 JSON -> API prompt dict"""
    if "nodes" not in ui:
        raise ValueError("这不是 UI 格式工作流（缺少 nodes）")

    ui = _expand_subgraphs(ui)

    links = {}
    for l in ui.get("links") or []:
        if isinstance(l, list):
            links[l[0]] = l
        elif isinstance(l, dict):
            links[l.get("id")] = [l.get("id"), l.get("origin_id"), l.get("origin_slot"),
                                 l.get("target_id"), l.get("target_slot"), l.get("type")]

    nodes_by_id = {str(n["id"]): n for n in ui["nodes"]}

    def resolve(lid, depth=0):
        """顺着 Reroute 找到真正的上游连线"""
        if lid is None or depth > 8:
            return None
        l = links.get(lid)
        if not l:
            return None
        origin = nodes_by_id.get(str(l[1]))
        if origin and origin.get("type") == "Reroute":
            for s in origin.get("inputs") or []:
                if s.get("link") is not None:
                    return resolve(s["link"], depth + 1)
            return None
        return l

    prompt = {}
    skipped = []
    for node in ui["nodes"]:
        ntype = node.get("type")
        if is_ui_only(ntype) or ntype == "PrimitiveNode":
            continue
        mode = node.get("mode", 0)
        if mode in (2, 4):        # 2=静音不执行, 4=旁路
            skipped.append((node.get("id"), ntype, mode))
            continue
        if node.get("id") in (None, 0):     # 前端临时节点
            continue
        info = object_info.get(ntype)
        if not info:
            raise ValueError(f"服务端没有节点类型 {ntype}（缺插件或名称不符）")

        order = info.get("input_order") or {}
        req = (info.get("input") or {}).get("required") or {}
        opt = (info.get("input") or {}).get("optional") or {}
        names = list(order.get("required") or []) + list(order.get("optional") or [])
        if not names:
            names = list(req.keys()) + list(opt.keys())

        # 1) 控件值按顺序落位
        inputs = {}
        wv = node.get("widgets_values")
        if isinstance(wv, dict):
            for k, v in wv.items():
                if not isinstance(v, (list, dict)):
                    inputs[k] = v
        elif isinstance(wv, list):
            idx = 0
            for name in names:
                spec = req.get(name, opt.get(name))
                if not isinstance(spec, list):
                    continue
                opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if opts.get("socketless"):
                    continue
                if is_widget_spec(spec):
                    if idx < len(wv):
                        inputs[name] = wv[idx]
                        idx += 1
                    if opts.get("control_after_generate") and idx < len(wv):
                        idx += 1          # seed 后面那个 randomize/fixed 不算 API 参数

        # 2) 连线覆盖
        for sock in node.get("inputs") or []:
            if sock.get("link") is None:
                continue
            l = resolve(sock["link"])
            if not l:
                continue
            origin = nodes_by_id.get(str(l[1]))
            if origin and origin.get("type") == "PrimitiveNode":
                continue          # 原语节点的值已经写在上面的控件里了
            inputs[sock["name"]] = [str(l[1]), l[2]]

        prompt[str(node["id"])] = {"class_type": ntype, "inputs": inputs, "_meta": {"title": node.get("title") or ntype}}

    for s in skipped:
        print(f"  · 跳过节点 #{s[0]} {s[1]}（mode={s[2]} 静音/旁路）", file=sys.stderr)
    return prompt


def nodes_of(prompt, class_type):
    return [(nid, n) for nid, n in prompt.items() if n["class_type"] == class_type]


# ---------- 参数注入 ----------
def apply_overrides(prompt, args, uploaded_image=None):
    applied = []

    if args.prompt is not None:
        target = None
        for nid, n in nodes_of(prompt, "PrimitiveStringMultiline"):
            n["inputs"]["value"] = args.prompt
            target = f"#{nid} PrimitiveStringMultiline"
        # 若提示词直接写在条件节点里（没走文本节点），也一起改
        for nid, n in nodes_of(prompt, "MiniMaxH3ImageConditioning"):
            if "prompt" in n["inputs"] and not isinstance(n["inputs"]["prompt"], list):
                n["inputs"]["prompt"] = args.prompt
                target = target or f"#{nid} 提示词"
        applied.append(f"提示词 -> {target or '未找到文本节点'}")

    if uploaded_image:
        for nid, n in nodes_of(prompt, "LoadImage"):
            n["inputs"]["image"] = uploaded_image
            applied.append(f"参考图 -> #{nid} LoadImage = {uploaded_image}")

    # 尺寸：优先 width/height，其次比例+像素
    if args.width and args.height:
        for nid, n in nodes_of(prompt, "MiniMaxH3ImageConditioning"):
            n["inputs"]["width"] = int(args.width)
            n["inputs"]["height"] = int(args.height)
            applied.append(f"尺寸 -> #{nid} = {args.width}x{args.height}")
    elif args.megapixels is not None or args.aspect:
        for nid, n in nodes_of(prompt, "ResolutionSelector"):
            if args.aspect:
                n["inputs"]["aspect_ratio"] = args.aspect
            if args.megapixels is not None:
                n["inputs"]["megapixels"] = float(args.megapixels)
            if args.multiple:
                n["inputs"]["multiple"] = int(args.multiple)
            applied.append(f"画布 -> #{nid} {n['inputs'].get('aspect_ratio')} / "
                           f"{n['inputs'].get('megapixels')}MP / x{n['inputs'].get('multiple')}")

    for nid, n in nodes_of(prompt, "KSampler"):
        if args.seed is not None:
            n["inputs"]["seed"] = int(args.seed)
            applied.append(f"seed -> {args.seed}")
        elif args.random_seed:
            n["inputs"]["seed"] = random.randint(0, 2**48)
            applied.append(f"seed -> 随机 {n['inputs']['seed']}")
        if args.steps is not None:
            n["inputs"]["steps"] = int(args.steps)
        if args.cfg is not None:
            n["inputs"]["cfg"] = float(args.cfg)
        if args.denoise is not None:
            n["inputs"]["denoise"] = float(args.denoise)

    if args.lora_strength is not None:
        for nid, n in nodes_of(prompt, "LoraLoaderModelOnly"):
            n["inputs"]["strength_model"] = float(args.lora_strength)
            applied.append(f"LoRA 权重 -> {args.lora_strength}")

    if args.prefix:
        for nid, n in nodes_of(prompt, "SaveImage"):
            n["inputs"]["filename_prefix"] = args.prefix
            applied.append(f"文件名前缀 -> {args.prefix}")

    return applied


def main():
    ap = argparse.ArgumentParser(description="用 ComfyUI API 自动出图")
    ap.add_argument("--server", default=os.environ.get("COMFY_SERVER", "http://192.168.0.172:7177"))
    ap.add_argument("--user", default=os.environ.get("COMFY_USER", "comfy"))
    ap.add_argument("--password", default=os.environ.get("COMFY_PASS", "Comfy-5b51ea51"))
    ap.add_argument("--workflow", required=True, help="工作流 JSON（UI 格式或 API 格式都可以）")
    ap.add_argument("--prompt", help="覆盖提示词")
    ap.add_argument("--image", help="参考图本地路径（自动上传到 input 目录）")
    ap.add_argument("--aspect", help='画布比例，如 "16:9 (Widescreen)" / "1:1 (Square)"')
    ap.add_argument("--megapixels", type=float, help="目标总像素（模型推荐 4 左右）")
    ap.add_argument("--multiple", type=int, help="尺寸对齐倍数，默认 32")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--random-seed", action="store_true")
    ap.add_argument("--steps", type=int)
    ap.add_argument("--cfg", type=float)
    ap.add_argument("--denoise", type=float)
    ap.add_argument("--lora-strength", type=float)
    ap.add_argument("--prefix", help="输出文件名前缀")
    ap.add_argument("--output", default="./comfy_out", help="下载目录")
    ap.add_argument("--timeout", type=int, default=1800, help="等待出图超时秒数")
    ap.add_argument("--dry-run", action="store_true", help="只打印转换结果，不提交")
    ap.add_argument("--save-api", action="store_true", help="把转换后的 API JSON 存盘")
    ap.add_argument("--skip-wait", action="store_true", help="提交后立即返回 prompt_id")
    args = ap.parse_args()

    client = Comfy(args.server, args.user, args.password)

    with open(args.workflow, "r", encoding="utf-8-sig") as f:
        wf = json.load(f)

    if "nodes" in wf:
        print("· 读取 UI 工作流，拉取 object_info 并转换 …", flush=True)
        oi = client.object_info()
        prompt = convert_ui_to_api(wf, oi)
    else:
        print("· 已是 API 格式，直接使用", flush=True)
        prompt = {k: v for k, v in wf.items() if isinstance(v, dict) and "class_type" in v}

    uploaded = None
    if args.image:
        uploaded = client.upload_image(args.image)
        print(f"· 参考图已上传: {uploaded}", flush=True)

    for line in apply_overrides(prompt, args, uploaded):
        print(f"· {line}", flush=True)

    if args.save_api:
        out_json = os.path.join(args.output, "api_prompt.json")
        os.makedirs(args.output, exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(prompt, f, ensure_ascii=False, indent=2)
        print(f"· API JSON 已保存: {out_json}", flush=True)

    if args.dry_run or args.save_api:
        if args.dry_run:
            print(json.dumps(prompt, ensure_ascii=False, indent=2))
        return 0
    if args.save_api and not args.dry_run:
        return 0

    # 提交前先看一眼队列，避免悄悄排很久
    q = client.queue_info()
    print(f"· 当前队列: 运行中 {len(q.get('queue_running', []))}，排队 {len(q.get('queue_pending', []))}", flush=True)

    res = client.submit(prompt)
    pid = res.get("prompt_id")
    errs = res.get("node_errors") or {}
    if errs:
        print("⚠ 节点校验有问题:", json.dumps(errs, ensure_ascii=False, indent=2))
    print(f"· 已提交 prompt_id={pid}，排队位置 {res.get('number')}", flush=True)
    if args.skip_wait:
        return 0

    t0 = time.time()
    entry = client.wait(pid, timeout=args.timeout)
    files = client.download_outputs(entry, args.output)
    print(f"✓ 完成，用时 {time.time()-t0:.1f}s，输出 {len(files)} 个文件:")
    for p in files:
        print("   " + os.path.abspath(p))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ComfyError as e:
        print(f"✗ ComfyUI 报错: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
