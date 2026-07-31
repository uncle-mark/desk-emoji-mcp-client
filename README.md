# Desk-Emoji MCP 客户端

Desk-Emoji MCP 客户端 是一个面向 Desk Emoji 设备的 WebSocket MCP 图形客户端和 Python SDK。它可以在局域网内通过 UDP 广播自动发现设备，也可以主动扫描网段；连接一台或多台设备后，客户端会把 MCP 工具调用广播到所有已连接设备，用于多设备联控、调试和自动化集成。

## 主要功能

- UDP 自动发现：监听 `desk-emoji.mcp.announce` 广播并自动加入候选 MCP 设备。
- 局域网扫描：按网段、端口和内置 WebSocket 路径探测可用 MCP 设备。
- 多设备连接：候选 server 以复选框列出，支持全选、连接和断开。
- 快捷联控：提供声音、头部、表情、时钟、设置菜单和字符显示等常用控制。
- 工具列表：连接后可发送 `tools/list`，勾选 `withUserTools: true` 后合并显示普通工具和用户工具。
- 调试日志：显示连接、发送、接收、发现和错误信息。
- Python SDK：通过 `desk_emoji_sdk.py` 在脚本中连接、扫描、发现和控制 Desk-Emoji MCP 设备。

## 环境要求

- Python 3.10 或更新版本
- `websocket-client`
- `customtkinter`
- `pillow`
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

1. 启动后进入 `控制` 页。
2. 等待 UDP 自动发现，或确认 `局域网搜索` 中的网段、端口和超时后点击 `搜索`。
3. 搜索或发现到的 server 会显示在左侧候选列表中，候选项默认勾选。
4. 按需点击 `全选` 或单独勾选设备。
5. 点击 `连接`，客户端会连接所选 server。
6. 连接成功后客户端会自动发送 `initialize`。
7. 使用右侧快捷控制区发送命令，命令会广播到所有已连接 server。
8. 在 `工具` 页点击 `列出工具`，查看各 server 返回的工具 schema、用户工具说明和自动生成的参数 JSON。
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
  "version": "v4.3.0"
}
```

### 主动扫描

`控制` 页的 `局域网搜索` 支持配置：

- `网段`：默认使用本机所在网段，例如 `192.168.1.0/24`。
- `端口`：图形客户端默认 `8765`，支持逗号或分号分隔。
- `路径`：图形客户端默认 `/mcp`，支持逗号或分号分隔；未以 `/` 开头时会自动补齐。
- `超时秒`：默认 `1.2` 秒。

扫描范围最多支持 1024 个 host。SDK 的 `scan_servers()` 默认会扫描端口 `8765,8000,8080,9000` 和路径 `/mcp`、`/`、`/xiaozhi/v1/`；图形客户端只扫描界面中填写的端口和路径。

## 快捷控制

`控制` 页右侧的快捷控制会调用以下 MCP 工具，并广播到所有已连接 server：

- 声音：`self.audio_speaker.set_volume`、`self.audio_speaker.set_mute`、`self.audio_speaker.play_sound`、`self.voice.start_listening`、`self.voice.stop_listening`
- 头部：`self.head.turn`、`self.head.center`、`self.head.nod`、`self.head.shake`、`self.head.roll`
- 表情：`self.emoji.play_gif`、`self.emoji.random_gif`、`self.emoji.set_eye`
- 模式：`self.clock.show_brief`、`self.clock.start_mode`、`self.clock.stop_mode`、`self.settings_menu.start_mode`、`self.settings_menu.stop_mode`
- 字符：`self.oled.show_text`、`self.oled.clear_text`
- OTA：`self.ota.set_url`、`self.ota.reset_url`

其中 `退出时钟模式` 会先调用 `self.clock.stop_mode`，再调用 `self.emoji.set_eye` 恢复当前选择的眼睛表情。

## Python SDK

SDK 可用于脚本、自动化测试或第三方应用集成。

单设备调用：

```python
from desk_emoji_sdk import DeskEmojiClient

with DeskEmojiClient("ws://192.168.1.20:8765/mcp") as client:
    client.initialize()
    client.center_head()
    client.play_sound("success")
    client.set_ota_url("https://example.com/ota/")
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

`DeskEmojiGroup.call_tool()` 会等待每台设备的 MCP 响应并返回 `{url: response}`，兼容旧脚本。群组常用封装方法默认采用 nowait 并行发送并返回 `{url: "sent"}`；需要读取响应时可传 `nowait=False`。需要让多台设备尽快同步执行动作时，也可以直接使用 nowait API：

```python
with DeskEmojiGroup(urls) as group:
    group.initialize()
    sent = group.call_tool_nowait("self.emoji.play_gif", {
        "name": "rocket",
        "loop_count": 3,
        "frame_delay_ms": 10,
        "hold_sec": 0,
    })
    print(sent)  # {url: "sent"}，失败项为 "error: ..."
```

GUI 群控按钮默认采用并行发送即完成；后续 MCP 响应仍会显示在日志中。

局域网扫描：

```python
from desk_emoji_sdk import scan_servers

candidates = scan_servers("192.168.1.0/24", timeout=1.2)
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

`discover_servers()` 会合并近期缓存、UDP 查询回复和快速 TCP 扫描结果，并尽量验证 UDP 候选是否在线。

UDP 持续监听：

```python
from desk_emoji_sdk import receive_udp_announcements

for candidate in receive_udp_announcements(timeout=None):
    print(candidate.url, candidate.version, candidate.source)
```

带 token 的设备可传入 `token`，SDK 会自动补齐 `Bearer ` 前缀：

```python
with DeskEmojiClient("ws://192.168.1.20:8765/mcp", token="YOUR_TOKEN") as client:
    client.initialize()
    client.set_volume(80)
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

客户端连接后会自动发送 `initialize`。工具页点击 `列出工具` 时会发送 `tools/list`，并按工具名称合并所有 server 返回的工具信息，列表中会显示每个工具来自几个 server。勾选 `用户工具` 时会携带 `withUserTools: true`，用于显示固件标记为用户可见的工具，包括：

- `self.audio_speaker.get_status`
- `self.audio_speaker.enable_output`
- `self.voice.start_listening`
- `self.voice.stop_listening`
- `self.get_system_info`
- `self.reboot`
- `self.upgrade_firmware`
- `self.ota.set_url`
- `self.ota.reset_url`
- `self.assets.set_download_url`

## 项目结构

```text
.
├── app.py                    # Tkinter 图形客户端
├── desk_emoji_sdk.py         # Python SDK
├── help_content.py           # 帮助页 Markdown 渲染
├── docs/SDK使用文档.md        # SDK 详细说明
├── docs/操作手册.md           # 图形客户端操作手册
├── requirements.txt          # Python 依赖
├── start.sh                  # macOS / Linux 启动脚本
└── start.bat                 # Windows 启动脚本
```

## 许可证

见 [LICENSE](LICENSE)。
