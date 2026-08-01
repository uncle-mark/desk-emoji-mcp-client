# Desk-Emoji MCP 客户端

Desk-Emoji MCP 客户端是用于连接和控制 Desk-Emoji 设备的桌面图形客户端，同时提供一个可独立使用的 Python SDK。它通过 WebSocket MCP 协议和设备通信，支持局域网自动发现、主动扫描、多设备群控、工具调用、日志查看和脚本化集成。

当前版本：`v4.3.1`

## 功能特性

- UDP 自动发现：监听 Desk-Emoji 设备广播，自动加入候选设备列表。
- 局域网扫描：按网段、端口和 WebSocket 路径主动探测 MCP 设备。
- 多设备连接：可勾选多台设备并统一连接、断开和控制。
- 快捷控制：支持音量、静音、提示音、头部动作、表情、时钟、设置菜单、字符显示和 OTA 地址设置。
- MCP 工具页：读取 `tools/list`，查看工具 schema，自动生成调用参数模板，并广播调用工具。
- 日志查看：显示发现、连接、发送、接收和错误信息，同时写入本地 `logs` 目录。
- Python SDK：提供单设备客户端、群组广播、UDP 发现和网段扫描能力。

## 环境要求

- Python 3.10 或更新版本
- `tkinter`
- `websocket-client`
- `customtkinter`
- `pillow`

`tkinter` 通常随 Python 自带。如果当前 Python 发行版不包含 `tkinter`，请安装系统对应组件，或改用带 `tkinter` 的 Python 发行版。

## 快速开始

macOS / Linux:

```bash
./start.sh
```

Windows:

```bat
start.bat
```

启动脚本会在当前目录创建 `.venv`，升级 `pip`，安装 `requirements.txt` 中的依赖，检查 `tkinter/customtkinter`，然后运行图形客户端。

也可以手动启动：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Windows 手动启动：

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

## 图形客户端使用

1. 启动客户端后进入 `控制` 页面。
2. 等待 UDP 自动发现，或在 `局域网搜索` 中填写网段、端口、路径和超时时间后点击 `搜索`。
3. 在候选设备列表中勾选要连接的设备。
4. 点击 `连接`。连接成功后客户端会自动发送 MCP `initialize`。
5. 使用右侧快捷控制区发送命令。命令会广播到所有已连接设备。
6. 在 `工具` 页面点击 `列出工具`，查看设备返回的 MCP 工具并手动调用。
7. 在 `日志` 页面查看连接、发现、请求、响应和错误记录。

左侧导航包含：

- `控制`：搜索、选择、连接设备，并使用常用快捷控制。
- `工具`：查看 MCP 工具列表、工具说明和调用参数。
- `日志`：查看运行日志，支持清空界面日志。
- `帮助`：阅读 `docs` 目录中的内置 Markdown 文档。

## 设备发现

### UDP 自动发现

客户端监听 UDP `37654` 端口。收到如下广播后，会把设备加入候选列表：

```json
{
  "type": "desk-emoji.mcp.announce",
  "transport": "websocket",
  "ip": "192.168.1.20",
  "port": 8765,
  "path": "/mcp",
  "version": "v4.3.1"
}
```

### 主动扫描

`控制` 页支持配置：

- `网段`：默认根据本机网络推断，例如 `192.168.1.0/24`。
- `端口`：默认 `8765`，可用英文逗号或分号分隔多个端口。
- `路径`：默认 `/mcp`，可用英文逗号或分号分隔多个路径。
- `超时秒`：默认 `1.2` 秒。

为避免误扫过大范围，扫描最多支持 1024 个 host。

## 快捷控制能力

快捷控制会调用设备端 MCP 工具，并广播到所有已连接设备：

- 声音：设置音量、静音、播放提示音、开始聆听、停止聆听。
- 头部：居中、转向、点头、摇头、左滚、右滚。
- 表情：播放指定 GIF、随机 GIF、显示眼睛表情。
- 模式：显示时钟、开启/退出时钟模式、打开/关闭设置菜单。
- 字符：显示不超过 10 个字符、清除字符显示。
- OTA：设置自定义 OTA 地址、恢复默认 OTA 地址。

OTA URL 需要使用 `https` 协议，包含有效主机名，长度为 10-512 个字符，且不能包含空白字符。

## Python SDK

SDK 位于 `desk_emoji_sdk.py`，可用于脚本、自动化测试或第三方集成。

单设备调用：

```python
from desk_emoji_sdk import DeskEmojiClient

with DeskEmojiClient("ws://192.168.1.20:8765/mcp") as client:
    client.initialize()
    client.center_head()
    client.play_sound("success")
    client.set_eye("happy")
```

带 token 的设备：

```python
from desk_emoji_sdk import DeskEmojiClient

with DeskEmojiClient("ws://192.168.1.20:8765/mcp", token="YOUR_TOKEN") as client:
    client.initialize()
    client.set_volume(80)
```

多设备广播：

```python
from desk_emoji_sdk import DeskEmojiGroup

urls = [
    "ws://192.168.1.20:8765/mcp",
    "ws://192.168.1.21:8765/mcp",
]

with DeskEmojiGroup(urls) as group:
    group.initialize()
    sent = group.play_gif("rocket")
    print(sent)
```

局域网扫描：

```python
from desk_emoji_sdk import scan_servers

candidates = scan_servers("192.168.1.0/24", ports=[8765], paths=["/mcp"], timeout=1.2)
for candidate in candidates:
    print(candidate.label)
```

组合发现：

```python
from desk_emoji_sdk import discover_servers

candidates = discover_servers("192.168.1.0/24")
for candidate in candidates:
    print(candidate.url, candidate.status, candidate.source)
```

UDP 持续监听：

```python
from desk_emoji_sdk import receive_udp_announcements

for candidate in receive_udp_announcements(timeout=None):
    print(candidate.url, candidate.version, candidate.source)
```

更多 SDK 用法见 [docs/SDK使用文档.md](docs/SDK使用文档.md)。

## MCP 消息格式

WebSocket 连接建立后，客户端会先发送 hello：

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "session-001"
}
```

后续 MCP 请求会放在设备固件使用的 WebSocket envelope 中：

```json
{
  "session_id": "session-001",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "self.head.center",
      "arguments": {}
    },
    "id": 1
  }
}
```

`工具` 页点击 `列出工具` 时会发送 `tools/list`。勾选 `用户工具` 后会携带 `withUserTools: true`。

## 项目结构

```text
.
├── app.py                     # CustomTkinter 图形客户端
├── desk_emoji_sdk.py          # Python SDK
├── help_content.py            # 帮助页 Markdown 加载与渲染
├── docs/
│   ├── SDK使用文档.md          # SDK 详细说明
│   └── 客户端手册.md           # 图形客户端操作手册
├── icons/                     # 界面图标资源
├── logs/                      # 运行日志目录
├── requirements.txt           # Python 依赖
├── start.sh                   # macOS / Linux 启动脚本
└── start.bat                  # Windows 启动脚本
```

## 常见问题

### 搜索不到设备

请确认电脑和 Desk-Emoji 设备在同一局域网，网段填写正确，设备 MCP 服务端口和路径匹配，并检查防火墙是否阻止 UDP 广播或 WebSocket 连接。

### 控制按钮发送失败

通常表示当前没有可用连接。请先在 `控制` 页搜索设备、勾选设备并点击 `连接`。

### 工具列表为空

请确认设备已连接，并在 `工具` 页点击了 `列出工具`。如果勾选了 `用户工具`，还需要设备固件支持返回用户工具。

## 文档

- [客户端手册](docs/客户端手册.md)
- [SDK 使用文档](docs/SDK使用文档.md)

## 许可证

见 [LICENSE](LICENSE)。
