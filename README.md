# 慧川 ComfyUI 工作台

把局域网里的 ComfyUI（`http://192.168.0.172:7177`）包成一套类似即梦/labnana 的深色工作台：首页对话式生成、生成过程流、资产库、共享盘、内嵌原版 ComfyUI 工作流页。

前端是单文件 `comfy_web/index.html`，后端是纯标准库的 `comfy_web/server.py`（Python 3.9+，无第三方依赖）。

## 目录

```
comfy_web/
  server.py            后端：出图、任务轮询、资产库、共享盘、/comfy/* 反代
  index.html           前端（单文件，无构建）
  config.example.json  配置模板，复制成 config.local.json 后填真实值
  data/                运行数据（records.json、auth.json），不进仓库
comfy_wf/              从 ComfyUI 拉下来的 16 个工作流（后端启动时同步）
comfy_gen.py           ComfyUI API 客户端 + UI 工作流转 API 格式
docs/index.html        GitHub Pages 版（由 tools/build-docs.ps1 生成）
tools/                 构建与发布脚本
```

## 本地跑起来

```powershell
cd comfy_web
copy config.example.json config.local.json   # 填 ComfyUI 地址 / 账号 / 密码 / 共享盘
python server.py --port 8787
```

或直接双击根目录的 `更新-本地.cmd`（会做语法自检 + 重启服务）。

浏览器打开 `http://127.0.0.1:8787`，用 ComfyUI 的 Basic Auth 账号登录。

**同局域网其它电脑**：服务默认监听 `0.0.0.0`，启动日志会打印 `局域网访问: http://192.168.x.x:8787`，别的电脑直接开这个地址（同源访问，没有跨域和 https 混合内容问题）。首次需要在 Windows 防火墙放行 8787 入站：

```powershell
netsh advfirewall firewall add rule name="Comfy Workbench 8787" dir=in action=allow protocol=TCP localport=8787
```

只给本机用时加 `--host 127.0.0.1`。

前端默认跟着「打开页面的那个地址」走：用 `http://192.168.x.x:8787` 打开就是同源，不会再去连 `127.0.0.1`；如果页面是从别的端口（比如临时起的静态服务）打开的，会自动推到同一台主机的 `8787`。所以别的电脑不要用 GitHub Pages 的 https 地址登录——https 页面连不上 http 后端，浏览器会直接拦掉，只会在登录页显示「连不上」。

## 配置

所有敏感信息都只在 `comfy_web/config.local.json`（已 gitignore），字段：

| 字段 | 说明 |
| --- | --- |
| `server` | ComfyUI 地址，如 `http://192.168.0.172:7177` |
| `user` / `password` | ComfyUI 的 Basic Auth 账号密码 |
| `share_server` | 共享盘地址（`http://192.168.0.172:8080`），留空则共享页自动置灰 |
| `port` | 服务端口，默认 8787 |
| `cors` | 允许跨域的前端来源，`*` 表示不限；线上直连时按域名收紧更安全 |

环境变量优先于文件：`COMFY_SERVER` / `COMFY_USER` / `COMFY_PASS` / `SHARE_SERVER` / `PORT` / `CW_CORS` / `CW_CONFIG`。

## 线上怎么跑（重要）

`192.168.0.172` 是局域网地址，**公网浏览器摸不到**；而 `server.py` 必须跑在一台能访问它的机器上。所以「线上站」有两种形态，别混着理解：

**A. GitHub Pages 只当前端壳子**

Pages 只能放静态文件，跑不了 Python。`docs/index.html` 是同一套界面，默认后端地址为空，需要这样指过去：

```
https://<你的账号>.github.io/<仓库>/?api=https://<你的后端域名>
```

后端得先能被公网访问（Cloudflare Tunnel / frp / 路由器端口映射随便一种），且必须是 **HTTPS** —— Pages 是 https，调 http 会被浏览器按混合内容拦掉。

**B. 后端跑在能连 ComfyUI 的机器上**

在同一台内网机器上跑 `server.py`，再用隧道把它暴露出去，Pages 前端就指向这个隧道域名。流程：本机 `python server.py` → 隧道 `cloudflared tunnel --url http://127.0.0.1:8787` → 拿到 https 域名 → 写进 Pages 的 `?api=`。

两侧靠 token 鉴权：登录接口除了写 Cookie，还会返回 `token`，前端存 localStorage 并带 `X-CW-Token` 头；图片走 `?t=` 参数。跨域请求由后端 `cors` 配置放行。

## 本地 / 线上互不覆盖

两套更新是两条独立通道，不会互相拉取或强制同步：

- **本地更新**：`更新-本地.cmd`（`tools/local-restart.ps1`）—— 只做语法自检、重启本机服务，不碰 GitHub。
- **线上更新**：`更新-线上.cmd`（先 `tools/build-docs.ps1` 生成 Pages 版，再 `tools/publish.ps1` 推送）—— 只推 GitHub，不重启本地服务。

推送不需要装 git：`publish.ps1` 用 GitHub REST API，逐文件算 git blob SHA 与远程对比，只上传有变化的文件，并同步删除本地已删掉的文件。

准备一次 token：GitHub → Settings → Developer settings → Personal access tokens，勾 `repo`（私有仓库）或 `public_repo`（公开仓库）+ `workflow`，写进根目录 `gh_token.txt`（已 gitignore），或设环境变量 `GH_TOKEN`。

```powershell
# 首次：建仓库 + 开 Pages
pwsh tools/build-docs.ps1
pwsh tools/publish.ps1 -Repo tinghai0724-cmd/huichuan-workbench -Create -Pages -Message "首次上线"

# 之后：只推改动
pwsh tools/publish.ps1 -Repo tinghai0724-cmd/huichuan-workbench -Message "首页改版"
```

## 排版与交互

- 纯黑底 + 琥珀金强调色，主色 `#023a91`，主按钮渐变 `#023a91 → #1f5cc0`
- 侧栏：首页 / 资产库 / 生成 / 工作流 / 共享 / 状态
- 「生成」是左右分栏：左边生成过程卡片流，右边保留完整生成抽屉
- 「工作流」内嵌原版 ComfyUI（`/comfy/*` 反代，自动代填 Basic Auth）
- 光标是双层的：光点跟手，光环缓动追赶；悬停控件放大、点击扩散涟漪；切页统一 0.34s 缓动入场

## 已知限制

- `/ws` 没做 WebSocket 转发（stdlib HTTP 服务器不支持协议升级），内嵌 ComfyUI 里跑任务时节点进度可能不实时；要看实时进度点「↗ 新窗口」。
- 共享盘依赖 `192.168.0.172:8080`，没配或不可达时共享页会显示未连接。
- `records.json` 只在本地，仓库不含任何出图记录。
- **公网暴露前注意**：`/comfy/*` 反代目前不校验登录（iframe 没法带请求头），相当于把 ComfyUI 直接开给任何拿到后端地址的人。用隧道暴露后端时，请在隧道层加访问策略（Cloudflare Access、Basic Auth），或只在可信网络里开。`/api/*` 都是要登录的。
