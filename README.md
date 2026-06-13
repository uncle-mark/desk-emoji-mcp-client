# Desk-Emoji MCP Client

Desk-Emoji MCP Client 是一个面向 Desk Emoji 设备的 WebSocket MCP 图形客户端和 Python SDK。它可以在局域网内通过 UDP 广播自动发现设备，也可以主动扫描网段；连接一台或多台设备后，客户端会把 MCP 工具调用广播到所有已连接设备，用于多设备联控、调试和自动化集成。

## 主要功能

- UDP 自动发现：监听 `desk-emoji.mcp.announce` 广播并自动加入候选 MCP server。
- 局域网扫描：按网段、端口和内置 WebSocket 路径探测可用 MCP server。
- 多设备连接：候选 server 以复选框列出，支持全选、连接和断开。
- 快捷联控：提供音频、头部、表情、时钟、设置菜单和字符显示等常用控制。
- 工具列表：连接后可发送 `tools/list`，合并显示所有 server 返回的工具 schema。
- 调试日志：显示连接、发送、接收、发现和错误信息。
- Python SDK：通过 `desk_emoji_sdk.py` 在脚本中连接、扫描、发现和控制 Desk-Emoji MCP 设备。

## 环境要求

- Python 3.10 或更新版本
- `websocket-client`
- `tkinter`

`tkinter` 通常随 Python 自带。如果当前 Python 发行版没有内置 `tkinter`，请按系统方式安装或更换包含 `tkinter` 的 Python 发行版。

## 快速启动

macOS / Linux:

```bash
./start.sh
```

Windows:

```bat
start.bat
```

启动脚本会在当前目录创建 `.venv`，安装依赖，检查 `tkinter`，然后运行图形客户端。

也可以手动安装并启动：

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

## 图形客户端使用流程

1. 启动后进入 `连接与控制` 页。
2. 等待 UDP 自动发现，或确认 `局域网搜索` 中的网段、端口和超时后点击 `搜索`。
3. 搜索或发现到的 server 会显示在左侧候选列表中，候选项默认勾选。
4. 按需点击 `全选` 或单独勾选设备。
5. 点击 `连接`，客户端会连接所选 server。
6. 连接成功后客户端会自动发送 `initialize`。
7. 使用右侧快捷控制区发送命令，命令会广播到所有已连接 server。
8. 在 `工具列表` 页点击 `列出工具`，查看各 server 返回的工具 schema 和自动生成的参数 JSON。
9. 在 `日志` 页查看 WebSocket 收发内容和错误信息，点击 `清空` 可清除日志。

## 发现方式

### UDP 自动发现

客户端启动后会监听 UDP `37654` 端口。收到如下广播时，会把对应 server 加入候选列表：

```json
{
  "type": "desk-emoji.mcp.announce",
  "transport": "websocket",
  "ip": "192.168.1.20",
  "port": 8765,
  "path": "/mcp",
  "version": "v4.1.0"
}
```

### 主动扫描

`连接与控制` 页的 `局域网搜索` 支持配置：

- `网段`：默认使用本机所在网段，例如 `192.168.1.0/24`。
- `端口`：默认 `8765,8000,8080,9000`，支持逗号或分号分隔。
- `超时秒`：默认 `0.6` 秒。

扫描范围最多支持 1024 个 host。客户端会对开放 TCP 端口继续探测内置 WebSocket 路径：`/mcp`、`/`、`/xiaozhi/v1/`。

## 快捷控制

`连接与控制` 页右侧的快捷控制会调用以下 MCP 工具，并广播到所有已连接 server：

- 音频：`self.audio_speaker.set_volume`、`self.audio_speaker.set_mute`、`self.audio_speaker.play_sound`
- 头部：`self.head.turn`、`self.head.center`、`self.head.nod`、`self.head.shake`、`self.head.roll`
- 表情：`self.emoji.play_gif`、`self.emoji.random_gif`、`self.emoji.set_eye`
- 模式：`self.clock.show_brief`、`self.clock.start_mode`、`self.clock.stop_mode`、`self.settings_menu.start_mode`、`self.settings_menu.stop_mode`
- 字符：`self.oled.show_code`

其中 `退出时钟模式` 会先调用 `self.clock.stop_mode`，再调用 `self.emoji.set_eye` 恢复当前选择的眼睛表情。

## Python SDK

SDK 可用于脚本、自动化测试或第三方应用集成。

单设备调用：

```python
from desk_emoji_sdk import DeskEmojiClient

with DeskEmojiClient("ws://192.168.1.20:8765/mcp") as client:
    client.initialize()
    client.call_tool("self.head.center")
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
    group.call_tool("self.emoji.play_gif", {
        "name": "rocket",
        "loop_count": 3,
        "frame_delay_ms": 10,
        "hold_sec": 0,
    })
```

局域网扫描：

```python
from desk_emoji_sdk import scan_servers

candidates = scan_servers("192.168.1.0/24", timeout=0.6)
for candidate in candidates:
    print(candidate.label)
```

UDP 发现：

```python
from desk_emoji_sdk import receive_udp_announcements

for candidate in receive_udp_announcements(timeout=None):
    print(candidate.url, candidate.version, candidate.source)
```

带 token 的设备可传入 `token`，SDK 会自动补齐 `Bearer ` 前缀：

```python
with DeskEmojiClient("ws://192.168.1.20:8765/mcp", token="YOUR_TOKEN") as client:
    client.initialize()
    client.call_tool("self.audio_speaker.set_volume", {"volume": 80})
```

更多 SDK 示例见 [docs/SDK使用文档.md](docs/SDK使用文档.md)。

## WebSocket MCP 消息格式

连接建立后，客户端会先发送 WebSocket hello：

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "session-001"
}
```

后续 MCP 请求会放在固件使用的 WebSocket envelope 中：

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

客户端连接后会自动发送 `initialize`。工具列表页点击 `列出工具` 时会发送 `tools/list`，并按工具名称合并所有 server 返回的工具信息，列表中会显示每个工具来自几个 server。

## 项目结构

```text
.
├── app.py                    # Tkinter 图形客户端
├── desk_emoji_sdk.py         # Python SDK
├── docs/SDK使用文档.md        # SDK 详细说明
├── requirements.txt          # Python 依赖
├── start.sh                  # macOS / Linux 启动脚本
└── start.bat                 # Windows 启动脚本
```

## 许可证

见 [LICENSE](LICENSE)。
