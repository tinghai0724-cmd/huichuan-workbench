import re, sys
p = r"C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\comfy_web\index.html"
s = open(p, encoding="utf-8").read()
m = re.search(r"<script>(.*)</script>", s, re.S)
code = m.group(1)
open(r"C:\Users\Administrator\Doubao\chats\2026-11-23\new-chat\tools\_check.js", "w", encoding="utf-8").write(code)
print("script chars:", len(code))
