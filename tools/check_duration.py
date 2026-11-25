import sys, json, time
sys.path.insert(0, r"C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\comfy_web")
import server  # noqa: E402

server.REG.sync()
time.sleep(1)
print("names:", server.REG.names)

for name in ["MINIMax H3 文生视频.json", "minimax h3 图生视频.json", "minimax H3 首尾帧.json"]:
    if name not in server.REG.names:
        print("skip", name)
        continue
    caps = server.REG.caps_of(name)
    api = server.REG.base_prompt(name)
    stack = caps.get("images") or []
    img = None
    server.apply_params(api, caps, text="test", aspect="16:9 (Widescreen)", megapixels=1.0,
                        width=None, height=None, seed=1234, lora=None, duration=10)
    d = caps.get("duration") or {}
    node = api.get(d.get("node"))
    print(name)
    print("   duration node:", d.get("node"), "value ->", node["inputs"].get("value") if node else None,
          " target:", d.get("target"), d.get("key"))
    print("   expr:", (api.get(str(d.get("target"))) or {}).get("inputs", {}).get("expression", "")[:60])
