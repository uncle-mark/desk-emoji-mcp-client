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
    result = client.call_tool("self.head.center")
    print(result)
```

带 token 的设备可传入 `token`，SDK 会自动补齐 `Bearer ` 前缀：

```python
with DeskEmojiClient(url, token="YOUR_TOKEN") as client:
    client.initialize()
    client.call_tool("self.audio_speaker.set_volume", {"volume": 80})
```

## 多设备广播

```python
from desk_emoji_sdk import DeskEmojiGroup

urls = [
    "ws://192.168.1.20:8765/mcp",
    "ws://192.168.1.21:8765/mcp",
]

with DeskEmojiGroup(urls) as group:
    group.initialize()
    responses = group.call_tool("self.emoji.play_gif", {
        "name": "rocket",
        "loop_count": 3,
        "frame_delay_ms": 10,
        "hold_sec": 0,
    })
    print(responses)
```

`DeskEmojiGroup.call_tool()` 返回 `{url: response}` 字典，便于定位每台设备的响应。

## 局域网扫描

```python
from desk_emoji_sdk import scan_servers

candidates = scan_servers("192.168.1.0/24", timeout=0.6)
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

## UDP 自动发现

```python
from desk_emoji_sdk import receive_udp_announcements

for candidate in receive_udp_announcements(timeout=None):
    print(candidate.url, candidate.version, candidate.source)
```

该方法监听 UDP `37654` 端口，并接收 `desk-emoji.mcp.announce` 广播。

## 常用工具示例

```python
client.call_tool("self.audio_speaker.set_volume", {"volume": 100})
client.call_tool("self.audio_speaker.set_mute", {"mute": False})
client.call_tool("self.audio_speaker.play_sound", {"name": "success"})

client.call_tool("self.head.turn", {"direction": "left", "offset": 10})
client.call_tool("self.head.nod", {"loop_count": 3})
client.call_tool("self.head.shake", {"loop_count": 3})
client.call_tool("self.head.center")

client.call_tool("self.emoji.play_gif", {
    "name": "rocket",
    "loop_count": 3,
    "frame_delay_ms": 10,
    "hold_sec": 0,
})
client.call_tool("self.emoji.random_gif", {
    "loop_count": 3,
    "frame_delay_ms": 10,
    "hold_sec": 0,
})
client.call_tool("self.emoji.set_eye", {"expression": "blink", "hold_sec": 0})

client.call_tool("self.clock.show_brief")
client.call_tool("self.clock.start_mode")
client.call_tool("self.clock.stop_mode")
client.call_tool("self.settings_menu.start_mode")
client.call_tool("self.settings_menu.stop_mode")
client.call_tool("self.oled.show_code", {"code": "HELLO"})
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
