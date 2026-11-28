#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI 本地 Web UI 服务 v2
------------------------------------------------
新增：
  · 多工作流切换 —— 自动同步 ComfyUI 工作流库，自动探测每个工作流的
    提示词节点 / 参考图节点 / 尺寸节点 / 采样器，前端按能力启用对应控件
  · 批量提交 —— 一次贴 N 条提示词，逐条入队

启动：python server.py --port 8787 --open
"""

import argparse
import base64
import collections
import copy
import csv
import io
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from comfy_gen import Comfy, ComfyError, convert_ui_to_api  # noqa: E402

def _load_config():
    """读取本机配置：config.local.json（不进仓库）> 环境变量 > 空。

    仓库里不带任何真实凭据，部署到别处只改配置即可。
    """
    path = os.environ.get("CW_CONFIG") or os.path.join(HERE, "config.local.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                        # noqa: BLE001
        return {}


_CFG = _load_config()

DEFAULTS = {
    "server": (os.environ.get("COMFY_SERVER") or _CFG.get("server")
               or "http://127.0.0.1:8188"),
    "user": os.environ.get("COMFY_USER") or _CFG.get("user") or "",
    "password": os.environ.get("COMFY_PASS") or _CFG.get("password") or "",
    "workflow_dir": (os.environ.get("COMFY_WF_DIR") or _CFG.get("workflow_dir")
                     or os.path.join(ROOT, "comfy_wf")),
    "port": int(os.environ.get("PORT") or _CFG.get("port") or 8787),
    "host": os.environ.get("CW_HOST") or _CFG.get("host") or "0.0.0.0",
    "default_workflow": _CFG.get("default_workflow") or "KOOK-Minimax-H3-image-尘星.json",
    "cors": os.environ.get("CW_CORS") or _CFG.get("cors") or "",
    # 只加载 ComfyUI 里打了书签（收藏）的工作流；设 false 则加载全部
    "only_favorites": (os.environ.get("CW_ONLY_FAVORITES") or "").lower()
                      not in ("0", "false", "no")
                      if (os.environ.get("CW_ONLY_FAVORITES") or "").strip()
                      else bool(_CFG.get("only_favorites", True)),
    # 书签之外仍要加载的工作流（没打星标但常用，比如首尾帧）
    "extra_workflows": [str(x) for x in (_CFG.get("extra_workflows") or []) if str(x).strip()],
    # 视频模式默认备选（按这个顺序排在前面）
    "video_workflows": [str(x) for x in (_CFG.get("video_workflows") or []) if str(x).strip()]
                       or ["minimax h3 图生视频.json", "MINIMax H3 文生视频.json",
                           "minimax H3 首尾帧.json"],
    # 视频时长（秒）：MiniMax H3 这档模型给的可选档位，min/max 用于校验
    "video_duration": (lambda d: {
        "min": float(d.get("min", 5)), "max": float(d.get("max", 15)),
        "step": float(d.get("step", 1)),
        "default": float(d.get("default", 5)),
        "presets": [float(x) for x in (d.get("presets") or [5, 10, 15])],
    })(_CFG.get("video_duration") or {}),
    # 节点替换：某些节点在本机跑不起来（比如 sageattention 版本不够）时换成语义等价、不依赖它的节点
    # 配成 {} 可以关掉
    "node_swaps": ({str(k): str(v) for k, v in _CFG["node_swaps"].items() if v}
                   if isinstance(_CFG.get("node_swaps"), dict)
                   else {"MiniMaxH3MemoryEfficientSageAttentionPatch": "MiniMaxLowVRAMAttention"}),
    # 每次同步都以 ComfyUI 上保存的工作流为准（在 ComfyUI 里改完节点，这边自动跟着更新）
    "sync_from_comfy": bool(_CFG.get("sync_from_comfy", True)),
}

DATA_DIR = os.path.join(HERE, "data")
RECORDS = os.path.join(DATA_DIR, "records.json")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DEFAULTS["workflow_dir"], exist_ok=True)

RATIOS = ["1:1 (Square)", "3:2 (Photo)", "3:4 (Portrait Standard)",
          "4:3 (Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)",
          "21:9 (Ultrawide)"]

SAMPLERS = {"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"}
TEXT_KEYS = ("text", "value", "prompt", "string", "text_prompt", "positive_prompt")
SIZE_FIRST = ("ResolutionSelector",)
# 承载「视频时长（秒）」的值节点
DURATION_SOURCES = ("PrimitiveFloat", "PrimitiveInt", "PrimitiveNumber", "Float", "INTConstant")

_lock = threading.Lock()

# ============================ 首页公告 ============================
NOTICE_FILE = os.path.join(DATA_DIR, "notice.json")
NOTICE_DEFAULT_TEXT = ("1. 视频生成建议在 1080p 的 1MP 像素点加载（耗时预计 16 分钟）\n"
                       "2. 图片和视频尽量不要同时运行")


def load_notice():
    try:
        with open(NOTICE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:                            # noqa: BLE001
        d = {}
    txt = d.get("text")
    if not isinstance(txt, str) or not txt.strip():
        txt = NOTICE_DEFAULT_TEXT
    return {"text": txt, "updated": int(d.get("updated") or 0), "by": d.get("by") or ""}


def save_notice(text, by=""):
    d = {"text": (text or "").strip() or NOTICE_DEFAULT_TEXT,
         "updated": int(time.time()), "by": by or ""}
    with open(NOTICE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d


# ============================ 工作流注册表 ============================
class Registry:
    """同步工作流库 + 转 API 格式 + 探测能力"""

    def __init__(self, client):
        self.client = client
        self.oi = None
        self.names = []
        self.files = {}
        self.base = {}          # name -> api prompt
        self.caps = {}          # name -> caps dict
        self.errors = {}        # name -> error text
        self._lock = threading.Lock()

    # ---------- 同步 ----------
    def favorites(self):
        """ComfyUI 工作流侧栏的书签（星标）：userdata/workflows/.index.json"""
        try:
            enc = urllib.parse.quote("workflows/.index.json", safe="")
            data = self.client.get("/api/userdata/" + enc)
            obj = data if isinstance(data, dict) else json.loads(data or "{}")
            fav = obj.get("favorites") or []
        except Exception:                        # noqa: BLE001
            return None
        names = []
        for f in fav:
            f = str(f)
            if f.startswith("workflows/"):
                f = f[len("workflows/"):]
            if f.lower().endswith(".json"):
                names.append(f)
        return names

    def sync(self):
        """同步工作流；ComfyUI 卡住时用短超时快速失败，别把启动拖死"""
        old = getattr(self.client, "timeout", 180)
        self.client.timeout = min(old, 12)
        try:
            return self._sync_inner()
        finally:
            self.client.timeout = old

    def _sync_inner(self):
        data = self.client.get("/api/userdata?dir=workflows")
        rows = data if isinstance(data, list) else json.loads(data or "[]")
        self.files = {}          # 每次同步都从零开始，避免切书签后残留旧条目
        # 只加载 ComfyUI 里打了书签（收藏）的工作流；没有书签就回落到全部
        if DEFAULTS.get("only_favorites", True):
            fav = self.favorites()
            extra = [n for n in (DEFAULTS.get("extra_workflows") or []) if n in rows and n not in (fav or [])]
            if fav or extra:
                wanted = set(fav or []) | set(extra)
                picked = [n for n in rows if n in wanted]
                if picked:
                    rows = picked
                    note = f"· 只加载书签工作流：{len(picked)} 个（共 {len(fav or [])} 个书签"
                    note += f" + {len(extra)} 个额外流程）" if extra else "）"
                    print(note)
                else:
                    print("· 书签里的工作流在 ComfyUI 上找不到，回落到全部")
            else:
                print("· 没有读到 ComfyUI 的工作流书签，回落到全部")
        wdir = DEFAULTS["workflow_dir"]
        for name in rows:
            path = os.path.join(wdir, name)
            remote = None
            # 默认以 ComfyUI 上的最新存档为准（在 ComfyUI 里改完节点，这边同步就跟着更新）
            if DEFAULTS.get("sync_from_comfy", True):
                try:
                    # ComfyUI 的 userdata 接口要把整个相对路径（含 /）一起编码
                    enc = urllib.parse.quote("workflows/" + name, safe="")
                    blob = self.client.get("/api/userdata/" + enc)
                    remote = (blob if isinstance(blob, bytes)
                              else json.dumps(blob, ensure_ascii=False).encode("utf-8"))
                except Exception as e:             # noqa: BLE001
                    self.errors[name] = f"{type(e).__name__}: {e}"
                    print(f"! 工作流下载失败 {name}: {e}")
            if remote is None:
                need = not os.path.exists(path) or os.path.getsize(path) < 400
                if need:
                    print(f"! {name} 本地没有可用副本，且没从 ComfyUI 拉到")
                    continue
            else:
                old = None
                if os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            old = f.read()
                    except Exception:               # noqa: BLE001
                        old = None
                if old != remote:
                    with open(path, "wb") as f:
                        f.write(remote)
                    with self._lock:
                        self.base.pop(name, None)
                        self.caps.pop(name, None)
                        self.errors.pop(name, None)
                    print(f"· 已按 ComfyUI 上的版本更新 {name}（{len(remote)} 字节）")
            self.files[name] = path
        self.names = sorted(self.files, key=lambda s: s)
        if self.names and DEFAULTS.get("default_workflow") not in self.names:
            ok_names = [n for n in self.names if self.caps_of(n).get("ok")] or self.names
            DEFAULTS["default_workflow"] = ok_names[0]
            print(f"· 默认工作流改到书签里的 {ok_names[0]}")
        return self.names

    def ensure_oi(self):
        if self.oi is None:
            self.oi = self.client.object_info()

    # ---------- 转换 ----------
    def base_prompt(self, name):
        with self._lock:
            if name in self.base:
                return copy.deepcopy(self.base[name])
        path = self.files.get(name)
        if not path:
            raise KeyError(f"未知工作流 {name}")
        self.ensure_oi()
        with open(path, "r", encoding="utf-8-sig") as f:
            wf = json.load(f)
        api = convert_ui_to_api(wf, self.oi) if "nodes" in wf else {
            k: v for k, v in wf.items() if isinstance(v, dict) and "class_type" in v
        }
        # 有些工作流是「整张图被旁路着」保存的（所有节点 mode=4），转出来是空的。
        # 这种情况把旁路当作启用再转一遍，否则会误判成「文生图 0 槽」。
        if "nodes" in wf and not _has_output(api):
            wf2 = copy.deepcopy(wf)
            for nd in wf2.get("nodes") or []:
                if nd.get("mode") in (2, 4):
                    nd["mode"] = 0
            try:
                api2 = convert_ui_to_api(wf2, self.oi)
            except Exception as e:                   # noqa: BLE001
                raise ValueError(f"工作流整张被旁路，恢复后仍无法转换：{type(e).__name__}: {e}")
            if _has_output(api2):
                print(f"· {name} 的节点都被旁路了，已按原节点恢复（{len(api2)} 节点）")
            api = api2
        _fix_value_links(api, wf)
        _swap_node_types(api, DEFAULTS.get("node_swaps") or {}, self.oi)
        with self._lock:
            self.base[name] = api
        return copy.deepcopy(api)

    # ---------- 能力探测 ----------
    def caps_of(self, name):
        with self._lock:
            if name in self.caps:
                return self.caps[name]
            if name in self.errors:
                return {"ok": False, "error": self.errors[name]}
        try:
            api = self.base_prompt(name)
            caps = probe(api)
            caps["ok"] = True
        except Exception as e:                       # noqa: BLE001
            caps = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            with self._lock:
                self.errors[name] = caps["error"]
            return caps
        with self._lock:
            self.caps[name] = caps
        return caps

    def warm(self):
        """后台预热：把所有工作流都转一遍、探一遍"""
        def run():
            for n in self.names:
                try:
                    self.caps_of(n)
                except Exception:                    # noqa: BLE001
                    pass
            print(f"· 工作流预热完成（{len(self.caps)} 可用 / {len(self.errors)} 异常）")
        threading.Thread(target=run, daemon=True).start()

    def summary(self):
        out = []
        for n in self.names:
            c = self.caps_of(n)
            out.append({
                "name": n,
                "label": n.replace(".json", ""),
                "ok": c.get("ok", False),
                "error": c.get("error"),
                "kind": c.get("kind", "?"),
                "has_prompt": bool(c.get("prompt")),
                "can_image": bool(c.get("images")),
                "image_slots": len(c.get("images") or []),
                "image_titles": c.get("image_titles") or [],
                "video": (c.get("kind") == "视频"),
                "size_mode": c.get("size_mode", "none"),
                "can_seed": bool(c.get("sampler")),
                "can_lora": bool(c.get("loras")),
                "can_duration": bool(c.get("duration")),
                "duration_default": (c.get("duration") or {}).get("value"),
            })
        return out


def _trace_text(nodes, ref, depth=0, seen=None):
    """从某条连线一路向上找「文本控件」节点"""
    if not isinstance(ref, list) or depth > 5:
        return None
    seen = seen or set()
    nid = str(ref[0])
    if nid in seen or nid not in nodes:
        return None
    seen.add(nid)
    node = nodes[nid]
    inputs = node.get("inputs") or {}
    for k, v in inputs.items():
        if isinstance(v, str) and k in TEXT_KEYS and v.strip():
            return (nid, k)
    # 先走条件/文本类输入，再兜底其它
    keys = [k for k in inputs if k in ("conditioning", "positive", "text", "string", "clip", "value")]
    keys += [k for k in inputs if k not in keys]
    for k in keys:
        got = _trace_text(nodes, inputs.get(k), depth + 1, seen)
        if got:
            return got
    return None


def _first_text_node(nodes):
    for nid, n in nodes.items():
        for k, v in (n.get("inputs") or {}).items():
            if isinstance(v, str) and k in TEXT_KEYS and v.strip():
                return (nid, k)
    # 空的也算（文生图模板常见空提示词）
    for nid, n in nodes.items():
        for k in (n.get("inputs") or {}):
            if k in TEXT_KEYS:
                return (nid, k)
    return None


OUTPUT_TYPES = ("SaveImage", "PreviewImage", "SaveVideo", "PreviewVideo",
                "VHS_VideoCombine", "SaveAnimatedWEBP", "SaveAnimatedPNG")


def _has_output(p):
    """有没有真正的输出节点（用来判断这张图是不是被整张旁路了）"""
    return any((n or {}).get("class_type") in OUTPUT_TYPES for n in p.values())


def _fix_value_links(api, wf):
    """把「连线指向纯前端值节点（rgthree Seed / PrimitiveFloat 等）」的输入改回字面量。

    这类节点不会被转进 API prompt，留着连线 ComfyUI 会报 KeyError。
    """
    if "nodes" not in wf:
        return api
    values = {}
    for nd in wf.get("nodes") or []:
        nid = str(nd.get("id"))
        if nid in api:
            continue
        wv = nd.get("widgets_values")
        val = None
        if isinstance(wv, list) and wv:
            val = wv[0]
        elif isinstance(wv, dict):
            for k in ("value", "seed", "number", "int", "float"):
                if k in wv:
                    val = wv[k]
                    break
        if isinstance(val, (int, float, str)) and not isinstance(val, bool):
            values[nid] = val
    for n in api.values():
        ins = n.get("inputs") or {}
        for k, v in list(ins.items()):
            if not (isinstance(v, list) and len(v) == 2):
                continue
            src = str(v[0])
            if src in values:
                ins[k] = values[src]
            elif src not in api:
                del ins[k]
    return api


def _swap_node_types(api, mapping, oi=None):
    """按配置把某类节点换成另一类（例如 Sage Attention Patch -> Low VRAM Attention）。

    mapping: {"原 class_type": "新 class_type"}；新节点必填但 prompt 里没有的参数，用 object_info 的默认值补上。
    """
    if not mapping:
        return api
    changed = []
    for nid, n in api.items():
        old = n.get("class_type")
        if old not in mapping:
            continue
        new = mapping[old]
        if not new or new == old:
            continue
        if oi is not None and new not in oi:
            print(f"· 节点替换跳过：服务端没有 {new}")
            continue
        n["class_type"] = new
        if oi is not None:
            req = ((oi.get(new) or {}).get("input") or {}).get("required") or {}
            for k, spec in req.items():
                if k in (n.get("inputs") or {}):
                    continue
                if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) \
                        and "default" in spec[1]:
                    n.setdefault("inputs", {})[k] = spec[1]["default"]
        changed.append((nid, old, new))
    for nid, old, new in changed:
        print(f"· 节点 #{nid} 已替换：{old} → {new}")
    return api


def probe(p):
    """从 API 格式 prompt 里探测可操作的节点"""
    nodes = p
    caps = {"nodes": len(nodes), "images": [], "loras": [], "size_mode": "none"}

    # 采样器 + 正向提示词
    sampler = next((nid for nid, n in nodes.items()
                    if n["class_type"] in SAMPLERS), None)
    caps["sampler"] = sampler
    prompt_target = None
    if sampler:
        prompt_target = _trace_text(nodes, nodes[sampler]["inputs"].get("positive"))
        if not prompt_target:
            prompt_target = _trace_text(nodes, nodes[sampler]["inputs"].get("conditioning"))
    if not prompt_target:
        prompt_target = _first_text_node(nodes)
    caps["prompt"] = prompt_target

    # 参考图位（按节点 id 排序，保证「首帧」排在「尾帧」前）
    img_nodes = [(nid, n) for nid, n in nodes.items()
                 if n["class_type"] in ("LoadImage", "LoadImageMask")]
    img_nodes.sort(key=lambda kv: (0, int(kv[0])) if str(kv[0]).isdigit() else (1, str(kv[0])))
    caps["images"] = [(nid, "image") for nid, _ in img_nodes]
    caps["image_titles"] = [((n.get("_meta") or {}).get("title") or n["class_type"])
                            for _, n in img_nodes]
    caps["image_list"] = [(nid, n["class_type"]) for nid, n in img_nodes]

    # 尺寸
    for nid, n in nodes.items():
        if n["class_type"] in SIZE_FIRST:
            caps["size_mode"] = "resolution"
            caps["size_node"] = nid
            break
    if caps["size_mode"] == "none":
        for nid, n in nodes.items():
            ins = n.get("inputs") or {}
            if "width" in ins and "height" in ins and not isinstance(ins["width"], list):
                caps["size_mode"] = "wh"
                caps["size_node"] = nid
                break

    # LoRA / 输出节点 / 视频批处理
    for nid, n in nodes.items():
        if n["class_type"] in ("LoraLoaderModelOnly", "LoraLoader"):
            caps["loras"].append(nid)

    # 时长（秒）：视频流程里通常是 PrimitiveFloat(秒) -> ComfyMathExpression -> length
    consumers = {}
    for nid, n in nodes.items():
        for k, v in (n.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2:
                consumers.setdefault(str(v[0]), []).append((nid, k))
    caps["duration"] = None
    for nid, n in nodes.items():
        if n["class_type"] not in DURATION_SOURCES:
            continue
        hit = None
        for cid, ck in consumers.get(nid, []):
            if ck in ("length", "duration", "seconds", "num_frames", "frames"):
                hit = (cid, ck)
                break
            if nodes[cid]["class_type"] == "ComfyMathExpression":
                for gid, gk in consumers.get(cid, []):
                    if gk in ("length", "duration", "seconds", "num_frames", "frames"):
                        hit = (gid, gk)
                        break
                if not hit:
                    hit = (nid, "value")          # 仍然通过这个值节点生效
                break
        if hit:
            title = ((n.get("_meta") or {}).get("title") or "")
            caps["duration"] = {"node": nid,
                                "value": n["inputs"].get("value"),
                                "target": hit[0], "key": hit[1], "title": title}
            break

    classes = {n["class_type"] for n in nodes.values()}
    if any("Video" in c or c.startswith("VHS_") for c in classes):
        caps["kind"] = "视频"
    elif any(c in ("BiRefNet", "ImageRemoveBackground", "RemoveBackground") or "Matting" in c
             or "Cutout" in c for c in classes):
        caps["kind"] = "抠图"
    elif any("Upscale" in c or "FlashVSR" in c for c in classes):
        caps["kind"] = "放大"
    elif caps["images"]:
        caps["kind"] = "图生图"
    else:
        caps["kind"] = "文生图"
    return caps


def apply_params(prompt, caps, *, text=None, image=None, images=None, aspect=None, megapixels=None,
                 width=None, height=None, seed=None, lora=None, prefix="comfy_ui", duration=None):
    """按能力把参数写进 API prompt"""
    touched = []

    if text is not None and caps.get("prompt"):
        nid, key = caps["prompt"]
        prompt[nid]["inputs"][key] = text
        touched.append("prompt")

    # 参考图：优先用 images 列表（最多填满工作流里所有 LoadImage 位），兼容旧的单图 image
    names = [x for x in (images or []) if x] or ([image] if image else [])
    slots = caps.get("images") or []
    bound = 0
    for (nid, key), name in zip(slots, names):
        prompt[nid]["inputs"][key] = name
        bound += 1
    # 图比 LoadImage 位多：给多出来的图各挂一个「加载图像」节点（LoadImage），
    # 让它们确实被送进工作流（用户可在 ComfyUI 里把它们接到对应输入上）。
    extras = names[len(slots):]
    for i, name in enumerate(extras, 1):
        nid = f"cw_ref_extra_{i}"
        while nid in prompt:
            nid += "_"
        prompt[nid] = {"class_type": "LoadImage",
                       "inputs": {"image": name, "upload": "image"},
                       "_meta": {"title": f"参考图 {len(slots) + i}"}}
        bound += 1
    if names:
        touched.append("image")

    if caps.get("size_mode") == "resolution":
        n = prompt[caps["size_node"]]["inputs"]
        if aspect:
            n["aspect_ratio"] = aspect
        if megapixels:
            n["megapixels"] = float(megapixels)
        n.setdefault("multiple", 32)
        touched.append("size")
    elif caps.get("size_mode") == "wh":
        n = prompt[caps["size_node"]]["inputs"]
        if width and height:
            n["width"], n["height"] = int(width), int(height)
            touched.append("size")
        elif aspect and megapixels:
            w, h = ratio_to_size(aspect, float(megapixels))
            n["width"], n["height"] = w, h
            touched.append("size")

    # 种子：留空 / -1 都按 ComfyUI 的「随机」语义处理（API 里 -1 不合法，必须换成正整数）
    try:
        seed = int(seed) if seed is not None and str(seed).strip() != "" else None
    except (TypeError, ValueError):
        seed = None
    seed_rnd = secrets.randbelow(2 ** 31 - 1)
    eff_seed = seed_rnd if (seed is None or seed < 0) else seed

    if caps.get("sampler"):
        ins = prompt[caps["sampler"]]["inputs"]
        for k in ("seed", "noise_seed"):
            if k in ins and not isinstance(ins[k], list):
                ins[k] = int(eff_seed)
                if "seed" not in touched:
                    touched.append("seed")
                break

    # 其余带 seed/noise_seed 的节点（如 RandomNoise）：指定了种子就统一写同一个，
    # 随机模式则把残留的负数洗成同一颗随机种子
    for nid, n in prompt.items():
        ins = n.get("inputs") or {}
        for k in ("seed", "noise_seed"):
            v = ins.get(k)
            if isinstance(v, list) or not isinstance(v, int) or isinstance(v, bool):
                continue
            if seed is not None and seed >= 0:
                ins[k] = int(seed)
            elif v < 0:
                ins[k] = int(seed_rnd)
            if "seed" not in touched:
                touched.append("seed")

    # 视频时长（秒）：写值节点，ComfyMathExpression 会把它换算成帧数
    if duration is not None and caps.get("duration"):
        d = caps["duration"]
        cfg = DEFAULTS.get("video_duration") or {}
        sec = float(duration)
        sec = max(float(cfg.get("min", 1)), min(float(cfg.get("max", 60)), sec))
        node = prompt.get(d["node"])
        if node is not None:
            node["inputs"]["value"] = sec
            touched.append("duration")

    if lora is not None and caps.get("loras"):
        for nid in caps["loras"]:
            prompt[nid]["inputs"]["strength_model"] = float(lora)
        touched.append("lora")

    for nid, n in prompt.items():
        if n["class_type"] in ("SaveImage", "SaveVideo", "VHS_VideoCombine"):
            if "filename_prefix" in n["inputs"] and not isinstance(n["inputs"]["filename_prefix"], list):
                n["inputs"]["filename_prefix"] = prefix
    return touched


def ratio_to_size(aspect, mp):
    ar = {"1:1 (Square)": 1.0, "4:3 (Standard)": 4 / 3, "3:2 (Photo)": 3 / 2,
          "16:9 (Widescreen)": 16 / 9, "3:4 (Portrait Standard)": 3 / 4,
          "2:3 (Portrait Photo)": 2 / 3, "9:16 (Portrait Widescreen)": 9 / 16,
          "21:9 (Ultrawide)": 21 / 9}
    r = ar.get(aspect, 16 / 9)
    w = (mp * 1e6 * r) ** 0.5
    h = w / r
    step = 32
    # 与前端一致：宽向上取到 32 的整数倍，高取最近的 32 倍数
    w32 = max(step, math.ceil(w / step) * step)
    h32 = max(step, round(h / step) * step)
    return w32, h32


# ============================ 数据存储 ============================
class Store:
    def __init__(self):
        self.items = []
        self.jobs = {}
        self.load()

    def load(self):
        if os.path.exists(RECORDS):
            try:
                with open(RECORDS, "r", encoding="utf-8") as f:
                    self.items = json.load(f)
            except Exception:                    # noqa: BLE001
                self.items = []

    def save(self):
        tmp = RECORDS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, RECORDS)

    def add(self, item):
        with _lock:
            self.items.insert(0, item)
            self.save()

    def update(self, prompt_id, **kw):
        with _lock:
            for it in self.items:
                if it.get("prompt_id") == prompt_id:
                    it.update(kw)
                    break
            self.save()

    def drop(self, prompt_id):
        with _lock:
            self.items = [i for i in self.items if i.get("prompt_id") != prompt_id]
            self.save()


# ============================ 文档库（上传表格/文本，解析成可复制的词条）============================

DOCS_DIR = os.path.join(DATA_DIR, "docs")
DOC_FILES = os.path.join(DOCS_DIR, "files")
DOC_INDEX = os.path.join(DOCS_DIR, "index.json")
MAX_DOC_BYTES = 40 * 1024 * 1024      # 单份文档上限 40MB
DOC_MAX_ROWS = 5000                   # 解析后最多保留的行
DOC_MAX_CELL = 3000                   # 单个单元格最多字符
DOC_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".srt", ".json"}
DOC_SHEET_EXT = {".xlsx", ".xlsm"}
DOC_WORD_EXT = {".docx"}
DOC_KEEP_EXT = DOC_TEXT_EXT | DOC_SHEET_EXT | DOC_WORD_EXT | {".pdf", ".doc", ".xls"}

XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _xlsx_col(ref):
    """A1 -> 0, B7 -> 1"""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_xlsx(raw):
    """纯标准库把 xlsx 拆成 [{name, rows[[...]]}]"""
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", XLSX_NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{XLSX_NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = []
    sh_el = wb.find("m:sheets", XLSX_NS)
    names = [s.get("name") or f"Sheet{i}" for i, s in
             enumerate(sh_el if sh_el is not None else [], 1)]
    for i, nm in enumerate(names, 1):
        p = f"xl/worksheets/sheet{i}.xml"
        if p not in z.namelist():
            continue
        sh = ET.fromstring(z.read(p))
        rows = []
        for row in sh.iter(f"{{{XLSX_NS['m']}}}row"):
            cells = {}
            for c in row.findall("m:c", XLSX_NS):
                t = c.get("t")
                v = c.find("m:v", XLSX_NS)
                inline = c.find("m:is", XLSX_NS)
                if t == "s" and v is not None:
                    try:
                        txt = shared[int(v.text)]
                    except Exception:            # noqa: BLE001
                        txt = ""
                elif inline is not None:
                    txt = "".join(x.text or "" for x in inline.iter(f"{{{XLSX_NS['m']}}}t"))
                else:
                    txt = v.text if v is not None else ""
                txt = (txt or "").strip()[:DOC_MAX_CELL]
                if txt:
                    cells[_xlsx_col(c.get("r"))] = txt
            if cells:
                rows.append([cells.get(j, "") for j in range(max(cells) + 1)])
            if len(rows) >= DOC_MAX_ROWS:
                break
        sheets.append({"name": nm, "rows": rows})
    return sheets


def parse_docx(raw):
    """取 word/document.xml 里的段落文本"""
    z = zipfile.ZipFile(io.BytesIO(raw))
    root = ET.fromstring(z.read("word/document.xml"))
    rows = []
    for p in root.iter(f"{{{WORD_NS['w']}}}p"):
        txt = "".join(t.text or "" for t in p.iter(f"{{{WORD_NS['w']}}}t")).strip()
        if txt:
            rows.append([txt[:DOC_MAX_CELL]])
        if len(rows) >= DOC_MAX_ROWS:
            break
    return [{"name": "正文", "rows": rows}]


def parse_text(raw, ext):
    data = raw
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        rows = []
        try:
            for r in csv.reader(io.StringIO(text), delimiter=delim):
                cells = [(c or "").strip()[:DOC_MAX_CELL] for c in r]
                if cells:
                    rows.append(cells)
                if len(rows) >= DOC_MAX_ROWS:
                    break
        except Exception:                        # noqa: BLE001
            rows = []
        return [{"name": ext.lstrip(".").upper(), "rows": rows}]
    rows = [[ln.strip()[:DOC_MAX_CELL]] for ln in text.split("\n") if ln.strip()]
    return [{"name": "文本", "rows": rows[:DOC_MAX_ROWS]}]


class DocStore:
    """data/docs/index.json 存元信息，原文件放 data/docs/files/"""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        os.makedirs(DOC_FILES, exist_ok=True)
        self.load()

    def load(self):
        if os.path.exists(DOC_INDEX):
            try:
                with open(DOC_INDEX, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.items = data.get("items", []) if isinstance(data, dict) else data
            except Exception:                    # noqa: BLE001
                self.items = []

    def save(self):
        tmp = DOC_INDEX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"items": self.items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DOC_INDEX)

    def add(self, name, raw):
        ext = os.path.splitext(name)[1].lower()
        if ext not in DOC_KEEP_EXT:
            raise ValueError(f"暂不支持 {ext or '这种'} 格式（可用 xlsx/csv/txt/md/docx/json/pdf）")
        if len(raw) > MAX_DOC_BYTES:
            raise ValueError("文件超过 40MB")
        did = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w\u4e00-\u9fa5.\-]+", "_", name)[:120] or ("doc" + ext)
        path = os.path.join(DOC_FILES, f"{did}_{safe}")
        with open(path, "wb") as f:
            f.write(raw)
        sheets, error = [], ""
        try:
            if ext in DOC_SHEET_EXT:
                sheets = parse_xlsx(raw)
            elif ext in DOC_WORD_EXT:
                sheets = parse_docx(raw)
            elif ext in DOC_TEXT_EXT:
                sheets = parse_text(raw, ext)
            else:
                error = "该格式只存档，暂不解析正文"
        except Exception as e:                   # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        rows_total = sum(len(s["rows"]) for s in sheets)
        item = {
            "id": did, "name": name, "ext": ext, "size": len(raw),
            "created": time.time(), "path": os.path.basename(path),
            "rows": rows_total, "error": error,
            "sheets": [{"name": s["name"], "rows": len(s["rows"])} for s in sheets],
        }
        with self.lock:
            self.items.insert(0, item)
            self.save()
        return item

    def find(self, did):
        return next((i for i in self.items if i.get("id") == did), None)

    def content(self, did):
        it = self.find(did)
        if not it:
            raise KeyError(did)
        path = os.path.join(DOC_FILES, it["path"])
        if not os.path.exists(path):
            raise FileNotFoundError(it["name"])
        with open(path, "rb") as f:
            raw = f.read()
        ext = it.get("ext") or ""
        if ext in DOC_SHEET_EXT:
            sheets = parse_xlsx(raw)
        elif ext in DOC_WORD_EXT:
            sheets = parse_docx(raw)
        elif ext in DOC_TEXT_EXT:
            sheets = parse_text(raw, ext)
        else:
            sheets = []
        return {"id": did, "name": it["name"], "ext": ext, "sheets": sheets,
                "error": it.get("error", "")}

    def raw_path(self, did):
        it = self.find(did)
        if not it:
            raise KeyError(did)
        return os.path.join(DOC_FILES, it["path"]), it

    def drop(self, did):
        with self.lock:
            it = self.find(did)
            if not it:
                return False
            try:
                os.remove(os.path.join(DOC_FILES, it["path"]))
            except Exception:                    # noqa: BLE001
                pass
            self.items = [i for i in self.items if i.get("id") != did]
            self.save()
        return True


DOCS = DocStore()


# ============================ 局域网共享（FileShare Pro）============================
SHARE_BASE = os.environ.get("SHARE_SERVER") or _CFG.get("share_server") or ""

# 局域网 AI 聊天平台（嵌在前端「AI」页里）
AI_BASE = (os.environ.get("AI_SERVER") or _CFG.get("ai_server")
           or "http://192.168.0.172:8787").rstrip("/")


def probe_ai(base=None, timeout=3.0):
    """探测 AI 聊天平台是否在线"""
    base = (base or AI_BASE).rstrip("/")
    t0 = time.time()
    try:
        req = urllib.request.Request(base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "url": base, "status": r.status,
                    "ms": int((time.time() - t0) * 1000)}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "url": base, "error": str(e),
                "ms": int((time.time() - t0) * 1000)}


class ShareHub:
    """以一个在线成员身份连到 FileShare Pro，缓存它的实时状态并提供操作"""

    def __init__(self, base, name="Comfy 工作台", device="workbench"):
        self.base = base.rstrip("/")
        self.name = name
        self.device = device
        self.state = {"users": [], "files": [], "chat": [], "clipboard": [],
                      "stats": {}, "net": {}, "lan": "", "me": None}
        self.connected = False
        self._stop = False
        self._thread = None
        self._beat = None
        self.id = self._load_id()

    def _load_id(self):
        p = os.path.join(DATA_DIR, "share_id.txt")
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
        except Exception:                        # noqa: BLE001
            pass
        nid = "workbench-" + uuid.uuid4().hex[:8]
        with open(p, "w", encoding="utf-8") as f:
            f.write(nid)
        return nid

    def start(self):
        if not self.base:                    # 没配共享盘就彻底静默，别刷错误
            self.connected = False
            return
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._beat = threading.Thread(target=self._heartbeat, daemon=True)
        self._beat.start()

    def _heartbeat(self):
        while not self._stop:
            try:
                self._post("/api/presence", {"id": self.id, "name": self.name, "device": self.device})
            except Exception:                    # noqa: BLE001
                pass
            time.sleep(8)

    def _loop(self):
        while not self._stop:
            try:
                q = urllib.parse.urlencode({"id": self.id, "name": self.name, "device": self.device})
                with urllib.request.urlopen(self.base + "/api/events?" + q, timeout=180) as r:
                    self.connected = True
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            msg = json.loads(line[5:].strip())
                        except Exception:            # noqa: BLE001
                            continue
                        self._apply(msg.get("type"), msg.get("payload"))
            except Exception:                    # noqa: BLE001
                self.connected = False
                time.sleep(3)

    def _apply(self, typ, payload):
        p = payload if isinstance(payload, dict) else {}
        with _lock:
            if typ == "snapshot":
                self.state.update({
                    "me": p.get("me"),
                    "users": p.get("users") or [],
                    "files": p.get("files") or [],
                    "chat": p.get("chat") or [],
                    "clipboard": p.get("clipboard") or [],
                    "stats": p.get("stats") or {},
                    "net": p.get("net") or {},
                    "lan": p.get("lan") or "",
                })
                if p.get("me"):
                    self.id = p["me"]
            elif typ == "presence":
                self.state["users"] = payload if isinstance(payload, list) else []
            elif typ == "files":
                self.state["files"] = p.get("files") or []
                self.state["stats"] = p.get("stats") or self.state["stats"]
            elif typ == "chat":
                if isinstance(payload, list):
                    self.state["chat"] = payload
                elif "chat" in p:
                    self.state["chat"] = p["chat"]
                elif p.get("text"):
                    self.state["chat"] = (self.state["chat"] + [p])[-200:]
            elif typ == "clipboard":
                if isinstance(payload, list):
                    self.state["clipboard"] = payload
                elif "clipboard" in p:
                    self.state["clipboard"] = p["clipboard"]
            elif typ == "stats" and isinstance(payload, dict):
                self.state["stats"] = payload
            elif typ == "net" and isinstance(payload, dict):
                self.state["net"] = payload

    def snapshot(self):
        with _lock:
            lan = self.state.get("lan")
            if isinstance(lan, list):
                lan = next((x.get("address") for x in lan
                            if isinstance(x, dict) and x.get("address")), "")
            elif isinstance(lan, dict):
                lan = lan.get("address") or ""
            return {
                "connected": self.connected,
                "disabled": not self.base,
                "name": self.name,
                "me": self.id,
                "users": self.state["users"],
                "files": self.state["files"],
                "chat": self.state["chat"],
                "clipboard": self.state["clipboard"],
                "stats": self.state["stats"],
                "net": self.state["net"],
                "lan": lan or "",
                "server": self.base,
            }

    # ---------- 操作 ----------
    def _post(self, path, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, text):
        return self._post("/api/chat", {"text": text, "from": self.name, "fromId": self.id})

    def clip(self, text):
        return self._post("/api/clipboard", {"text": text, "from": self.name})

    def clear_chat(self):
        return self._post("/api/chat/clear", {})

    def clear_clip(self):
        return self._post("/api/clipboard/clear", {})

    def delete_file(self, fid):
        req = urllib.request.Request(self.base + f"/api/file/{fid}", method="DELETE")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def download(self, fid):
        with urllib.request.urlopen(self.base + f"/api/download/{fid}", timeout=300) as r:
            return r.read(), r.headers.get("Content-Type") or "application/octet-stream"

    def upload(self, filename, data, mime=None):
        boundary = "----share" + uuid.uuid4().hex
        mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {mime}\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = head + data + tail
        q = urllib.parse.urlencode({"uploader": self.name})
        req = urllib.request.Request(self.base + "/api/upload?" + q, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))


SHARE = ShareHub(SHARE_BASE)


# ============================ 服务 ============================
CLIENT = Comfy(DEFAULTS["server"], DEFAULTS["user"], DEFAULTS["password"])
REG = Registry(CLIENT)
STORE = Store()

# ============================ 登录 / Basic Auth ============================
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
SESSION_TTL = 7 * 24 * 3600
COOKIE = "cw_sid"
_sessions = {}          # token -> {"user": ..., "ts": ...}
_auth_lock = threading.Lock()


def _load_auth():
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                        # noqa: BLE001
        return {}


def _save_auth(d):
    try:
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:                        # noqa: BLE001
        pass


def apply_creds(user, password):
    """把凭据装到全局 Comfy 客户端 + DEFAULTS（上传/观看图都用它）"""
    DEFAULTS["user"], DEFAULTS["password"] = user, password
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    CLIENT.auth = "Basic " + token


def verify_credentials(user, password, server=None, timeout=15):
    """用给定账号真实请求 ComfyUI，成功则返回 (True, meta)"""
    srv = (server or DEFAULTS["server"]).rstrip("/")
    c = Comfy(srv, user, password, timeout=timeout)
    t0 = time.time()
    ss = c.system_stats()
    qq = c.queue_info()
    dev = (ss.get("devices") or [{}])[0]
    return True, {
        "server": srv,
        "gpu": (dev.get("name") or "").split(":")[-1].strip(),
        "vram_free_gb": round((dev.get("vram_free") or 0) / 1024 ** 3, 1),
        "version": (ss.get("system") or {}).get("comfyui_version"),
        "ms": int((time.time() - t0) * 1000),
        "queue": len(qq.get("queue_running", [])) + len(qq.get("queue_pending", [])),
    }


def probe_server(timeout=5):
    """不带凭据探活：401 也算在线，只有连不上才判离线"""
    srv = DEFAULTS["server"].rstrip("/")
    t0 = time.time()
    try:
        urllib.request.urlopen(urllib.request.Request(srv + "/api/queue", method="GET"),
                               timeout=timeout)
        online = True
    except urllib.error.HTTPError:
        online = True                      # 401 / 403：服务在，只是要凭据
    except Exception as e:                 # noqa: BLE001
        return {"online": False, "ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"}
    return {"online": online, "ms": int((time.time() - t0) * 1000)}


def new_session(user):
    tok = secrets.token_urlsafe(24)
    with _auth_lock:
        _sessions[tok] = {"user": user, "ts": time.time()}
    return tok


def drop_session(headers):
    tok = _token_of(headers)
    if tok:
        _sessions.pop(tok, None)


def _cookie_token(headers):
    raw = headers.get("Cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and v:
            return v
    return None


def _token_of(headers, query=None):
    """会话 token：Cookie / X-CW-Token 头 / ?t= 参数 三者任一。

    跨域部署时浏览器带不上 Cookie，前端改用 X-CW-Token 头；图片没法加头，
    就退化成 URL 上的 ?t=。
    """
    tok = _cookie_token(headers)
    if tok:
        return tok
    tok = headers.get("X-CW-Token")
    if tok:
        return tok.strip()
    if query:
        tok = (query.get("t") or [""])[0]
        if tok:
            return tok.strip()
    return None


def _tokens_of(headers, query=None):
    """列出所有候选 token：Cookie / X-CW-Token 头 / ?t= 参数。

    浏览器里可能残留一个已失效的 HttpOnly Cookie，而前端手里是刚换来的
    有效 token；因此不能只认第一个，交给 session_of 逐个校验。
    """
    out = []
    tok = _cookie_token(headers)
    if tok:
        out.append(tok)
    tok = (headers.get("X-CW-Token") or "").strip()
    if tok:
        out.append(tok)
    if query:
        tok = (query.get("t") or [""])[0].strip()
        if tok:
            out.append(tok)
    return out


def session_of(headers, query=None):
    for tok in _tokens_of(headers, query):
        s = _sessions.get(tok)
        if not s:
            continue
        if time.time() - s["ts"] > SESSION_TTL:
            _sessions.pop(tok, None)
            continue
        s["ts"] = time.time()
        return s
    return None


# 启动时沿用上次验证过的凭据（没有就用默认 comfy / Comfy-5b51ea51）
_saved = _load_auth()
if _saved.get("username") and _saved.get("password"):
    try:
        DEFAULTS["server"] = _saved.get("server") or DEFAULTS["server"]
        apply_creds(_saved["username"], _saved["password"])
    except Exception:                        # noqa: BLE001
        pass


def watch(pid):
    STORE.jobs[pid] = {"status": "queued"}
    deadline = time.time() + 5400
    started = False
    while time.time() < deadline:
        time.sleep(2)
        try:
            hist = CLIENT.get(f"/api/history/{pid}")
        except ComfyError as e:
            STORE.jobs[pid] = {"status": "error", "message": str(e)}
            STORE.update(pid, status="error", message=str(e))
            return
        if pid in hist:
            entry = hist[pid]
            status = (entry.get("status") or {}).get("status_str")
            outs = []
            for node_out in (entry.get("outputs") or {}).values():
                for key in ("images", "gifs", "videos"):
                    for item in node_out.get(key, []) or []:
                        outs.append({"filename": item.get("filename"),
                                     "subfolder": item.get("subfolder", ""),
                                     "type": item.get("type", "output"),
                                     "kind": key})
            if status == "error":
                msg = "执行失败"
                for m in (entry.get("status") or {}).get("messages", []):
                    if m[0] == "execution_error":
                        msg = (m[1] or {}).get("exception_message", msg)
                STORE.jobs[pid] = {"status": "error", "message": msg}
                STORE.update(pid, status="error", message=msg)
                return
            STORE.jobs[pid] = {"status": "done", "outputs": outs}
            STORE.update(pid, status="done", files=outs, finished=time.time())
            return
        q = CLIENT.queue_info()
        running = any(j[1] == pid for j in q.get("queue_running", []))
        if running and not started:
            started = True
            STORE.update(pid, run_start=time.time())
        STORE.jobs[pid] = {"status": "running" if running else "queued"}
    STORE.jobs[pid] = {"status": "error", "message": "等待超时"}


def estimate_seconds(workflow):
    """按同工作流历史「真正开跑→出图」的时长估秒数（不含排队等待）。"""
    spans = []
    for it in STORE.items:
        if it.get("workflow") != workflow:
            continue
        s, f = it.get("run_start"), it.get("finished")
        if s and f and f > s and it.get("status") == "done":
            spans.append(f - s)
    spans = spans[:6]
    if not spans:
        return 30
    spans.sort()
    return max(5, min(240, int(spans[len(spans) // 2])))


STALE_SECONDS = 180


def _collect_outputs(entry):
    outs = []
    for node_out in (entry.get("outputs") or {}).values():
        for key in ("images", "gifs", "videos"):
            for item in node_out.get(key, []) or []:
                outs.append({"filename": item.get("filename"),
                             "subfolder": item.get("subfolder", ""),
                             "type": item.get("type", "output"),
                             "kind": key})
    return outs


def job_status(pid):
    """给生成过程页用的状态快照：排队位次 / 预计时长 / 已用时间 / 结果文件。"""
    item = next((i for i in STORE.items if i.get("prompt_id") == pid), None) or {}
    job = STORE.jobs.get(pid) or {}
    running_pids, pending_pids = [], []
    queue_ok = False
    try:
        qq = CLIENT.queue_info()
        running_pids = [j[1] for j in qq.get("queue_running", [])]
        pending_pids = [j[1] for j in qq.get("queue_pending", [])]
        queue_ok = True
    except Exception:                        # noqa: BLE001
        pass
    ahead = None
    if pid in pending_pids:
        ahead = len(running_pids) + pending_pids.index(pid)
    elif pid in running_pids:
        ahead = 0
    status = job.get("status") or item.get("status") or "queued"
    message = job.get("message") or item.get("message") or ""
    files = item.get("files") or job.get("outputs") or []
    # 队列里已经没有它、但记录仍停在 queued/running：先尝试从 history 捞结果，
    # 捞不到再判为中断（否则会话流里会永久挂着一张「生成中」）。
    if status not in ("done", "error") and queue_ok \
            and pid not in running_pids and pid not in pending_pids:
        hist = None
        try:
            h = CLIENT.get(f"/api/history/{pid}")
            if pid in h:
                hist = h[pid]
        except Exception:                    # noqa: BLE001
            hist = None
        if hist:
            st = (hist.get("status") or {}).get("status_str")
            outs = _collect_outputs(hist)
            if st == "error":
                message = "执行失败"
                for m in (hist.get("status") or {}).get("messages", []):
                    if m[0] == "execution_error":
                        message = (m[1] or {}).get("exception_message", message)
                status = "error"
                STORE.update(pid, status="error", message=message)
            else:
                status = "done"
                files = outs
                STORE.update(pid, status="done", files=outs, finished=time.time())
            STORE.jobs[pid] = {"status": status, "outputs": outs, "message": message}
        elif time.time() - (item.get("created") or 0) > STALE_SECONDS:
            status = "error"
            message = "任务已中断：ComfyUI 队列里已没有这个任务，请重新生成"
            STORE.update(pid, status="error", message=message)
            STORE.jobs[pid] = {"status": "error", "message": message}
    if status in ("done", "error"):
        ahead = 0
    base = item.get("run_start") if status == "running" and item.get("run_start") else item.get("created")
    created = base or 0
    return {
        "prompt_id": pid,
        "status": status,
        "ahead": ahead,
        "queue_running": len(running_pids),
        "queue_pending": len(pending_pids),
        "elapsed": max(0, int(time.time() - created)) if created else 0,
        "est": estimate_seconds(item.get("workflow")),
        "message": message,
        "files": files,
        "item": item,
    }


def reconcile_stale():
    """服务启动后清掉「僵尸任务」：记录还停在 queued/running，但 ComfyUI 队列里早已没有它。"""
    time.sleep(3)
    try:
        qq = CLIENT.queue_info()
        live = {j[1] for j in qq.get("queue_running", [])} | {j[1] for j in qq.get("queue_pending", [])}
    except Exception:                        # noqa: BLE001
        return
    changed = 0
    for it in list(STORE.items):
        if it.get("status") in ("done", "error"):
            continue
        pid = it.get("prompt_id")
        if not pid or pid in live:
            continue
        try:
            if pid in CLIENT.get(f"/api/history/{pid}"):
                job_status(pid)              # 让 history 里的结果回填
                continue
        except Exception:                    # noqa: BLE001
            pass
        if time.time() - (it.get("created") or 0) <= STALE_SECONDS:
            continue
        STORE.update(pid, status="error",
                     message="任务已中断：ComfyUI 队列里已没有这个任务，请重新生成")
        STORE.jobs[pid] = {"status": "error", "message": "任务已中断"}
        changed += 1
    if changed:
        print(f"· 已清理 {changed} 个中断任务")


def submit_one(*, workflow, text, image=None, images=None, aspect, megapixels, width, height,
               seed, lora, prefix, origin=None, duration=None):
    caps = REG.caps_of(workflow)
    if not caps.get("ok"):
        raise ComfyError(400, f"工作流不可用：{caps.get('error')}")
    api = REG.base_prompt(workflow)
    names = [x for x in (images or []) if x] or ([image] if image else [])
    apply_params(api, caps, text=text, image=image, images=names, aspect=aspect,
                 megapixels=megapixels, width=width, height=height, seed=seed, lora=lora,
                 prefix=prefix, duration=duration)
    res = CLIENT.submit(api)
    if res.get("node_errors"):
        raise ComfyError(200, json.dumps(res["node_errors"], ensure_ascii=False))
    pid = res["prompt_id"]
    slots_n = len(caps.get("images") or [])
    item = {
        "id": pid, "prompt_id": pid, "workflow": workflow, "prompt": text,
        "aspect": aspect, "megapixels": megapixels, "width": width, "height": height,
        "seed": seed, "image": (names or [None])[0], "images": names, "status": "queued",
        "created": time.time(), "files": [], "origin": origin or "",
        "image_slots": slots_n, "image_bound": min(len(names), slots_n),
        "image_extra": max(0, len(names) - slots_n),
        "kind": ("视频" if caps.get("kind") == "视频" else ""),
        "duration": (float(duration) if duration is not None else None),
    }
    STORE.add(item)
    threading.Thread(target=watch, args=(pid,), daemon=True).start()
    return pid


class Handler(BaseHTTPRequestHandler):
    server_version = "ComfyWorkbench/2.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- 跨域（线上页面直连本机后端时用） ----------
    def _cors(self):
        origin = self.headers.get("Origin")
        if not origin:
            return
        allow = DEFAULTS.get("cors") or ""
        if allow and allow != "*":
            if origin not in [x.strip() for x in allow.split(",") if x.strip()]:
                return
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-CW-Token, Range, Access-Control-Request-Private-Network")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Chrome 的 Private Network Access：公网 https 页面访问 127.0.0.1 需要显式放行
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- 输出 ----------
    def _json(self, obj, code=200, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype="application/octet-stream", cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 页面本身不缓存：改完 index.html 直接刷新就能看到新版本
        self.send_header("Cache-Control", cache)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # ---------- 原版 ComfyUI 反代（给「工作流控制」页用） ----------
    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _proxy_comfy(self, method="GET"):
        """把 /comfy/... 透传给 ComfyUI，并代填 Basic Auth（iframe 里不用再弹认证框）。"""
        rest = self.path[len("/comfy"):]           # 保留 path + query
        if not rest.startswith("/"):
            rest = "/" + rest
        url = DEFAULTS["server"].rstrip("/") + rest
        hdrs = {"Authorization": "Basic " + base64.b64encode(
            f"{DEFAULTS['user']}:{DEFAULTS['password']}".encode()).decode()}
        for h in ("Accept", "Content-Type", "Range", "If-None-Match"):
            v = self.headers.get(h)
            if v:
                hdrs[h] = v
        body = None
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data, code, hd = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            data, code, hd = e.read(), e.code, e.headers
        ctype = hd.get("Content-Type") or "application/octet-stream"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # HEAD 请求不写 body
        if method != "HEAD":
            self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b"{}"

    def _payload(self):
        try:
            return json.loads(self._body().decode("utf-8") or "{}")
        except Exception:                        # noqa: BLE001
            return {}

    # ---------- 登录 ----------
    def _login(self):
        p = self._payload()
        user = (p.get("username") or "").strip()
        pwd = p.get("password") or ""
        server = (p.get("server") or DEFAULTS["server"]).strip()
        if not user or not pwd:
            return self._json({"error": "请填写用户名和密码", "need_login": True}, 400)
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        try:
            ok, meta = verify_credentials(user, pwd, server, timeout=15)
        except ComfyError as e:
            if e.status in (401, 403):
                return self._json({"error": "账号或密码不正确（ComfyUI 返回 401）"}, 401)
            if e.status == 0:
                return self._json({"error": f"连不上 {server}：{e.body}"}, 502)
            return self._json({"error": f"ComfyUI 返回 {e.status}：{e.body[:200]}"}, 502)
        except Exception as e:                   # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        apply_creds(user, pwd)
        _save_auth({"server": meta["server"], "username": user,
                    "password": pwd, "verified": time.time()})
        tok = new_session(user)
        cookie = f"{COOKIE}={tok}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"
        return self._json({"ok": True, "user": user, "meta": meta, "token": tok},
                          cookie=cookie)

    # ---------- 鉴权 ----------
    OPEN_PATHS = ("/api/login", "/api/login/status", "/api/logout")

    def _guard(self):
        """未登录时直接回 401；返回 True 表示放行"""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if session_of(self.headers, query):
            return True
        self._json({"error": "未登录或登录已失效", "need_login": True}, 401)
        return False

    def _origin(self):
        """提交方 IP，用于在工作台里区分是哪台电脑发起的"""
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return fwd or (self.client_address[0] if self.client_address else "")

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            if path == "/comfy":
                return self._redirect("/comfy/")
            if path.startswith("/comfy/"):
                return self._proxy_comfy("GET")

            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._bytes(f.read(), "text/html; charset=utf-8")

            if path == "/api/login/status":
                sess = session_of(self.headers)
                info = {
                    "authed": bool(sess),
                    "user": (sess or {}).get("user"),
                    "server": DEFAULTS["server"],
                    "default_user": DEFAULTS["user"],
                    "probe": probe_server(),
                }
                if sess:
                    try:
                        _, meta = verify_credentials(DEFAULTS["user"], DEFAULTS["password"], timeout=8)
                        info.update({"online": True, "meta": meta})
                    except Exception as e:                       # noqa: BLE001
                        info.update({"online": False, "error": str(e)})
                return self._json(info)

            if path.startswith("/api/") and path not in self.OPEN_PATHS:
                if not self._guard():
                    return

            if path == "/api/state":
                vids = [n for n in (DEFAULTS.get("video_workflows") or [])
                        if n in REG.names and REG.caps_of(n).get("ok")]
                # 没配上的视频流程，退而用探测出来是「视频」的流程
                if not vids:
                    vids = [n for n in REG.names
                            if REG.caps_of(n).get("ok") and REG.caps_of(n).get("kind") == "视频"]
                return self._json({
                    "items": STORE.items,
                    "jobs": STORE.jobs,
                    "ratios": RATIOS,
                    "server": DEFAULTS["server"],
                    "workflows": REG.summary() if REG.names else [],
                    "default_workflow": DEFAULTS["default_workflow"],
                    "video_workflows": vids,
                    "video_duration": DEFAULTS.get("video_duration"),
                    "share_server": SHARE_BASE,
                    "ai_server": AI_BASE,
                    "only_favorites": bool(DEFAULTS.get("only_favorites", True)),
                })

            if path == "/api/ai/state":
                return self._json(probe_ai())

            if path == "/api/notice":
                return self._json(load_notice())

            if path == "/api/workflows":
                return self._json({"workflows": REG.summary()})

            # ---- 文档库 ----
            if path == "/api/docs":
                return self._json({"items": DOCS.items})

            if path == "/api/doc":
                did = (q.get("id") or [""])[0]
                if not did:
                    return self._json({"error": "missing id"}, 400)
                try:
                    return self._json(DOCS.content(did))
                except KeyError:
                    return self._json({"error": "文档不存在"}, 404)
                except FileNotFoundError as e:
                    return self._json({"error": f"原文件丢失：{e}"}, 404)

            if path == "/api/doc/raw":
                did = (q.get("id") or [""])[0]
                if not did:
                    return self._json({"error": "missing id"}, 400)
                try:
                    fp, it = DOCS.raw_path(did)
                except KeyError:
                    return self._json({"error": "文档不存在"}, 404)
                with open(fp, "rb") as f:
                    data = f.read()
                ctype = mimetypes.guess_type(it["name"])[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''"
                                 + urllib.parse.quote(it["name"]))
                self.end_headers()
                self.wfile.write(data)
                return

            if path == "/api/img":
                fn = (q.get("filename") or [""])[0]
                sub = (q.get("subfolder") or [""])[0]
                typ = (q.get("type") or ["output"])[0]
                if not fn:
                    return self._json({"error": "missing filename"}, 400)
                data = CLIENT.get("/api/view?" + urllib.parse.urlencode(
                    {"filename": fn, "subfolder": sub, "type": typ}))
                ctype = mimetypes.guess_type(fn)[0] or "image/png"
                # 视频要能拖动进度：支持 Range（浏览器播 mp4 会先发 bytes=0-）
                rng = (self.headers.get("Range") or "").strip()
                total = len(data)
                if rng.startswith("bytes=") and total:
                    part = rng[6:].split(",")[0]
                    a, _, b = part.partition("-")
                    try:
                        start = int(a) if a else 0
                        end = int(b) if b else total - 1
                    except ValueError:
                        start, end = 0, total - 1
                    end = min(end, total - 1)
                    if start > end:
                        start = 0
                    chunk = data[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "max-age=300")
                    self._cors()
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(chunk)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(total))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "max-age=300")
                self._cors()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                return

            if path == "/api/share/state":
                SHARE.start()
                return self._json(SHARE.snapshot())

            if path == "/api/share/download":
                fid = (q.get("id") or [""])[0]
                if not fid:
                    return self._json({"error": "missing id"}, 400)
                data, ctype = SHARE.download(fid)
                name = next((f.get("name", fid) for f in SHARE.snapshot()["files"]
                             if f.get("id") == fid), fid)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 "inline; filename*=UTF-8''" + urllib.parse.quote(name))
                self.send_header("Cache-Control", "max-age=600")
                self.end_headers()
                self.wfile.write(data)
                return

            if path == "/api/health":
                ss = CLIENT.system_stats()
                dev = (ss.get("devices") or [{}])[0]
                qq = CLIENT.queue_info()
                return self._json({
                    "ok": True,
                    "gpu": (dev.get("name") or "").split(":")[-1].strip(),
                    "vram_free_gb": round((dev.get("vram_free") or 0) / 1024 ** 3, 1),
                    "vram_total_gb": round((dev.get("vram_total") or 0) / 1024 ** 3, 1),
                    "running": len(qq.get("queue_running", [])),
                    "pending": len(qq.get("queue_pending", [])),
                })

            if path == "/api/genstatus":
                pid = (q.get("pid") or [""])[0]
                if not pid:
                    return self._json({"error": "missing pid"}, 400)
                return self._json(job_status(pid))

            # 未知路径统一兜底给原版 ComfyUI：内嵌页会用到 /assets、/extensions、
            # /scripts、/api/xxx 这类绝对路径请求，工作台没占用的都该落到 ComfyUI。
            return self._proxy_comfy("GET")
        except ComfyError as e:
            return self._json({"error": str(e)}, 502)
        except Exception as e:                   # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            if path.startswith("/comfy/"):
                return self._proxy_comfy("POST")

            if path == "/api/login":
                return self._login()

            if path == "/api/logout":
                drop_session(self.headers)
                return self._json({"ok": True},
                                  cookie=f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")

            if path.startswith("/api/") and path not in self.OPEN_PATHS:
                if not self._guard():
                    return

            if path == "/api/doc/upload":
                name = (q.get("name") or [""])[0].strip()
                raw = self._body()
                if not name:
                    return self._json({"error": "缺少文件名"}, 400)
                if not raw:
                    return self._json({"error": "文件是空的"}, 400)
                try:
                    item = DOCS.add(name, raw)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"ok": True, "item": item})

            if path == "/api/doc/delete":
                p = self._payload()
                did = p.get("id") or ""
                if not did:
                    return self._json({"error": "missing id"}, 400)
                return self._json({"ok": DOCS.drop(did)})

            if path == "/api/upload":
                raw = self._body()
                req = urllib.request.Request(
                    DEFAULTS["server"] + "/api/upload/image", data=raw, method="POST",
                    headers={"Content-Type": self.headers.get("Content-Type", "")})
                tok = base64.b64encode(
                    f"{DEFAULTS['user']}:{DEFAULTS['password']}".encode()).decode()
                req.add_header("Authorization", "Basic " + tok)
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read().decode("utf-8"))
                sub = data.get("subfolder", "")
                name = data.get("name", "")
                return self._json({"name": f"{sub}/{name}" if sub else name})

            if path == "/api/generate":
                p = self._payload()
                pid = submit_one(
                    workflow=p.get("workflow") or DEFAULTS["default_workflow"],
                    text=(p.get("prompt") or "").strip(),
                    image=p.get("image"), images=p.get("images"), aspect=p.get("aspect"),
                    megapixels=p.get("megapixels"), width=p.get("width"), height=p.get("height"),
                    seed=p.get("seed"), lora=p.get("lora"), prefix="comfy_ui",
                    origin=self._origin(), duration=p.get("duration"))
                it = next((i for i in STORE.items if i.get("prompt_id") == pid), {}) or {}
                return self._json({"prompt_id": pid,
                                   "image_slots": it.get("image_slots", 0),
                                   "image_bound": it.get("image_bound", 0),
                                   "image_extra": it.get("image_extra", 0)})

            if path == "/api/batch":
                p = self._payload()
                prompts = [x.strip() for x in (p.get("prompts") or []) if str(x).strip()]
                if not prompts:
                    return self._json({"error": "没有可用的提示词"}, 400)
                ids, errs = [], []
                for text in prompts:
                    try:
                        pid = submit_one(
                            workflow=p.get("workflow") or DEFAULTS["default_workflow"],
                            text=text, image=p.get("image"), images=p.get("images"), aspect=p.get("aspect"),
                            megapixels=p.get("megapixels"), width=p.get("width"),
                            height=p.get("height"), seed=p.get("seed"),
                            lora=p.get("lora"), prefix="comfy_ui", origin=self._origin(),
                            duration=p.get("duration"))
                        ids.append(pid)
                    except ComfyError as e:
                        errs.append(f"{text[:24]}… → {e}")
                return self._json({"accepted": len(ids), "prompt_ids": ids, "errors": errs})

            if path == "/api/rerun":
                p = self._payload()
                src = next((i for i in STORE.items if i.get("prompt_id") == p.get("prompt_id")), None)
                if not src:
                    return self._json({"error": "找不到这条记录"}, 404)
                pid = submit_one(
                    workflow=src.get("workflow") or DEFAULTS["default_workflow"],
                    text=src.get("prompt"), image=src.get("image"), aspect=src.get("aspect"),
                    megapixels=src.get("megapixels"), width=src.get("width"), height=src.get("height"),
                    seed=p.get("seed"), lora=p.get("lora"), prefix="comfy_ui",
                    origin=self._origin(),
                    duration=(p.get("duration") or src.get("duration")))
                return self._json({"prompt_id": pid})

            if path == "/api/delete":
                STORE.drop(self._payload().get("prompt_id"))
                return self._json({"ok": True})

            if path == "/api/share/chat":
                p = self._payload()
                text = (p.get("text") or "").strip()
                if not text:
                    return self._json({"error": "内容为空"}, 400)
                SHARE.start()
                return self._json(SHARE.chat(text[:2000]))

            if path == "/api/share/clip":
                p = self._payload()
                text = p.get("text") or ""
                if not text.strip():
                    return self._json({"error": "内容为空"}, 400)
                SHARE.start()
                return self._json(SHARE.clip(text))

            if path == "/api/share/chat/clear":
                SHARE.start()
                return self._json(SHARE.clear_chat())

            if path == "/api/share/clip/clear":
                SHARE.start()
                return self._json(SHARE.clear_clip())

            if path == "/api/share/delete":
                fid = self._payload().get("id")
                if not fid:
                    return self._json({"error": "missing id"}, 400)
                return self._json(SHARE.delete_file(fid))

            if path == "/api/share/upload":
                raw = self._body()
                q = urllib.parse.urlencode({"uploader": SHARE.name})
                req = urllib.request.Request(
                    SHARE.base + "/api/upload?" + q, data=raw, method="POST",
                    headers={"Content-Type": self.headers.get("Content-Type", "")})
                with urllib.request.urlopen(req, timeout=600) as r:
                    return self._json(json.loads(r.read().decode("utf-8")))

            if path == "/api/share/import":
                fid = self._payload().get("id")
                if not fid:
                    return self._json({"error": "missing id"}, 400)
                data, _ctype = SHARE.download(fid)
                name = next((f.get("name", "share.bin") for f in SHARE.snapshot()["files"]
                             if f.get("id") == fid), "share.bin")
                tmp = os.path.join(DATA_DIR, "import_" + str(int(time.time())) + "_" + name)
                with open(tmp, "wb") as f:
                    f.write(data)
                up = CLIENT.upload_image(tmp)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return self._json({"name": up, "filename": name})

            if path == "/api/share/push":
                p = self._payload()
                fn = p.get("filename")
                sub = p.get("subfolder") or ""
                typ = p.get("type") or "output"
                if not fn:
                    return self._json({"error": "missing filename"}, 400)
                data = CLIENT.get("/api/view?" + urllib.parse.urlencode(
                    {"filename": fn, "subfolder": sub, "type": typ}))
                SHARE.start()
                return self._json(SHARE.upload(fn, data))

            if path == "/api/notice":
                p = self._payload()
                sess = session_of(self.headers) or {}
                return self._json(save_notice(p.get("text") or "", sess.get("user") or "匿名"))

            if path == "/api/share/push-batch":
                # 资产库多选分享：一次把多张作品推到共享盘
                p = self._payload()
                items = p.get("items") or []
                if not items:
                    return self._json({"error": "missing items"}, 400)
                SHARE.start()
                pushed, failed = [], []
                for it in items[:60]:
                    fn = (it or {}).get("filename")
                    if not fn:
                        continue
                    sub = it.get("subfolder") or ""
                    typ = it.get("type") or "output"
                    try:
                        data = CLIENT.get("/api/view?" + urllib.parse.urlencode(
                            {"filename": fn, "subfolder": sub, "type": typ}))
                        if isinstance(data, dict) and data.get("error"):
                            raise ComfyError(data["error"])
                        res = SHARE.upload(fn, data)
                        if isinstance(res, dict) and (res.get("ok") is False or res.get("error")):
                            raise ComfyError(str(res.get("error") or "共享盘拒绝"))
                        pushed.append(fn)
                    except Exception as e:              # noqa: BLE001
                        failed.append({"filename": fn, "error": f"{type(e).__name__}: {e}"})
                return self._json({"ok": len(pushed), "pushed": pushed, "failed": failed})

            if path == "/api/refresh-workflows":
                REG.sync()
                REG.caps.clear()
                REG.base.clear()
                REG.errors.clear()
                REG.warm()
                return self._json({"workflows": REG.summary()})

            # 未知 POST 同样兜底给 ComfyUI（内嵌页的 /prompt、/api/* 等）
            return self._proxy_comfy("POST")
        except ComfyError as e:
            return self._json({"error": str(e)}, 502)
        except Exception as e:                   # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def bootstrap():
    # ComfyUI 刚开机/正忙时第一次同步常常失败，这里重试几次，别留下空列表
    names = []
    for attempt in range(1, 6):
        try:
            names = REG.sync() or []
        except Exception as e:                   # noqa: BLE001
            print(f"! 工作流同步失败（第 {attempt} 次）：{type(e).__name__}: {e}")
        else:
            if names:
                break
            print(f"! 第 {attempt} 次同步没有拿到工作流，稍后重试")
        time.sleep(8)
    print(f"· 已同步工作流 {len(names)} 个")
    try:
        REG.ensure_oi()
        REG.warm()
    except Exception as e:                       # noqa: BLE001
        print(f"! 预热失败：{e}")
    try:
        SHARE.start()
        print(f"· 共享服务已接入：{SHARE.base}（身份 {SHARE.name}）")
    except Exception as e:                       # noqa: BLE001
        print(f"! 共享服务连接失败：{e}")


def lan_ips():
    """本机所有内网 IPv4，用来在启动日志里直接告诉用户别人该访问哪个地址"""
    def usable(ip):
        # 排除回环、链路本地、以及 198.18/198.19 这类 VPN 基准测试网段
        return not ip.startswith(("127.", "0.", "169.254.", "198.18.", "198.19."))

    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if usable(ip) and ip not in out:
                out.append(ip)
    except Exception:                            # noqa: BLE001
        pass
    if not out:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if usable(ip):
                out.append(ip)
        except Exception:                        # noqa: BLE001
            pass
    # 常见家用/办公网段排前面
    out.sort(key=lambda ip: (0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2))
    return out


def main():
    ap = argparse.ArgumentParser(description="ComfyUI 本地工作台 v2")
    ap.add_argument("--port", type=int, default=DEFAULTS["port"])
    ap.add_argument("--host", default=DEFAULTS["host"],
                    help="监听地址，默认 0.0.0.0（同局域网其它电脑可访问）；只给本机用可传 127.0.0.1")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    # 先占住端口再后台预热：ComfyUI 卡住/关掉也不影响工作台自身启动
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=bootstrap, daemon=True).start()
    threading.Thread(target=reconcile_stale, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"· 工作台已启动: {url}   （监听 {args.host}:{args.port}，后端 {DEFAULTS['server']}）")
    for ip in lan_ips():
        print(f"· 局域网访问: http://{ip}:{args.port}")
    if args.open:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n· 已退出")


if __name__ == "__main__":
    main()
