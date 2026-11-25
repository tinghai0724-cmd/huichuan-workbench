import sys
sys.path.insert(0, r"C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\comfy_web")
import server  # noqa: E402

server.REG.sync()
for name in ["MINIMax H3 文生视频.json", "minimax h3 图生视频.json", "minimax H3 首尾帧.json"]:
    api = server.REG.base_prompt(name)
    print("==", name, "nodes:", sorted(api.keys(), key=lambda x: int(x)))
    for nid in ("1", "2", "3", "4", "5"):
        if nid in api:
            print("  ", nid, api[nid]["class_type"], api[nid].get("inputs"))
