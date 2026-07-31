# Desk-Emoji Python SDK 使用文档

`desk_emoji_sdk.py` 提供 Desk-Emoji WebSocket MCP 设备的 Python 调用接口，可用于脚本、自动化测试或第三方应用集成。

## 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

SDK 使用 `websocket-client` 连接设备；`tkinter` 只用于图形客户端，脚本调用 SDK 不依赖它。

## 单设备调用

```python
from desk_emoji_sdk import DeskEmojiClient

url = "ws://192.168.1.20:8765/mcp"

with DeskEmojiClient(url) as client:
    print(client.initialize())
    print(client.list_tools())
    print(client.list_tools(with_user_tools=True))
    result = client.center_head()
    print(result)
    print(client.play_sound("success"))
```

带 token 的设备可传入 `token`，SDK 会自动补齐 `Bearer ` 前缀：

```python
with DeskEmojiClient(url, token="YOUR_TOKEN") as client:
    client.initialize()
    client.set_volume(80)
```

`DeskEmojiClient.call_tool(name, arguments=None)` 仍可调用任意 MCP 工具；常用设备能力也提供了薄封装方法，便于脚本直接调用。

## 多设备广播

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

`DeskEmojiGroup.call_tool()` 返回 `{url: response}` 字典，便于定位每台设备的响应。

`DeskEmojiGroup.call_tool()`、`initialize()` 和 `broadcast()` 会等待每台设备的 MCP 响应，适合需要读取工具结果或兼容旧脚本的场景。群组常用封装方法默认使用 nowait 并行发送，返回 `{url: "sent"}`，失败项为 `error: ...`。需要读取响应时可传 `nowait=False`：

```python
with DeskEmojiGroup(urls) as group:
    group.initialize()
    responses = group.play_gif("rocket", nowait=False)
    print(responses)
```

需要让多台设备尽快同步收到同一条指令时，也可以直接使用 nowait API：

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

`call_tool_nowait()` 内部并行发送 `tools/call`，发送完成后立即返回，不等待工具执行结果。对应的通用方法是 `broadcast_nowait(method, params=None)`；如需并行初始化且不等待响应，可使用 `initialize_nowait()`。

图形客户端的群控按钮默认使用并行发送即完成；每台设备后续 MCP 响应仍会继续显示在日志中。

## 常用封装方法

单设备 `DeskEmojiClient` 和多设备 `DeskEmojiGroup` 都支持以下常用封装：

- 声音：`set_volume(volume)`、`set_mute(mute)`、`play_sound(name)`、`start_listening()`、`stop_listening()`
- 头部：`turn_head(direction, offset=10)`、`center_head()`、`nod_head(loop_count=3)`、`shake_head(loop_count=3)`、`roll_head(direction)`
- 表情：`play_gif(name, loop_count=3, frame_delay_ms=10, hold_sec=0)`、`random_gif(...)`、`set_eye(expression="blink", hold_sec=0)`
- 模式：`show_clock_brief()`、`start_clock_mode()`、`stop_clock_mode()`、`start_settings_menu()`、`stop_settings_menu()`
- 字符和 OTA：`show_text(code)`、`clear_text()`、`set_ota_url(url)`、`reset_ota_url()`

`DeskEmojiGroup` 的这些封装额外支持 `nowait=True/False` 参数，默认 `True`。

## 局域网扫描

```python
from desk_emoji_sdk import scan_servers

candidates = scan_servers("192.168.1.0/24", timeout=1.2)
for candidate in candidates:
    print(candidate.label)
```

默认扫描端口为 `8765,8000,8080,9000`，默认路径为 `/mcp`、`/`、`/xiaozhi/v1/`。可按需覆盖：

```python
candidates = scan_servers(
    "192.168.1.0/24",
    ports=[8765],
    paths=["/mcp"],
    token="YOUR_TOKEN",
    timeout=1.0,
)
```

为避免误扫过大网段，默认最多扫描 1024 个 host。

## 组合发现

```python
from desk_emoji_sdk import discover_servers

candidates = discover_servers("192.168.1.0/24")
for candidate in candidates:
    print(candidate.url, candidate.version, candidate.status, candidate.source)
```

`discover_servers()` 会合并三类候选：

- 近期缓存：默认保留最近 10 分钟发现过的候选。
- UDP 查询回复：向 `37654` 端口发送 `desk-emoji.mcp.discover` 查询，并接收设备返回的 `desk-emoji.mcp.announce`。
- 快速扫描：默认扫描 `8765` 端口和 `/mcp` 路径。

函数会尽量验证 UDP 候选是否能完成 WebSocket MCP `initialize`，最终按在线状态和 URL 排序返回。

## UDP 自动发现

```python
from desk_emoji_sdk import receive_udp_announcements

for candidate in receive_udp_announcements(timeout=None):
    print(candidate.url, candidate.version, candidate.source)
```

该方法监听 UDP `37654` 端口，并接收 `desk-emoji.mcp.announce` 广播。

如果只想主动发起一次 UDP 查询并收集回复，可使用：

```python
from desk_emoji_sdk import query_udp_announcements

for candidate in query_udp_announcements(listen_timeout=1.2):
    print(candidate.url, candidate.source)
```

## 常用工具示例

```python
client.set_volume(100)
client.set_mute(False)
client.play_sound("success")
client.start_listening()
client.stop_listening()

client.turn_head("left", offset=10)
client.nod_head(loop_count=3)
client.shake_head(loop_count=3)
client.center_head()
client.roll_head("left")

client.play_gif("rocket", loop_count=3, frame_delay_ms=10, hold_sec=0)
client.random_gif(loop_count=3, frame_delay_ms=10, hold_sec=0)
client.set_eye("blink", hold_sec=0)

client.show_clock_brief()
client.start_clock_mode()
client.stop_clock_mode()
client.start_settings_menu()
client.stop_settings_menu()
client.show_text("HELLO")
client.clear_text()

# 用户专用工具需要先用 with_user_tools=True 才会出现在 tools/list 中。
client.list_tools(with_user_tools=True)
client.set_ota_url("https://example.com/ota/")
client.reset_ota_url()
```

## 协议说明

连接建立后，SDK 会先发送 WebSocket hello：

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "session-001"
}
```

后续 MCP 请求会封装为：

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

`DeskEmojiClient.request(method, params)` 可发送自定义 MCP 方法，适合新增工具或调试固件扩展能力。
