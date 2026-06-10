# Desk-Emoji MCP Client

这是一个用于 Desk Emoji 的 MCP 图形客户端。它可以在局域网内自动发现或主动扫描 MCP server，勾选多个 server 后统一连接，并把 MCP 工具调用广播到所有已连接设备，实现多设备联控。

## 功能

- UDP 自动发现：监听 `desk-emoji.mcp.announce` 广播，自动加入 WebSocket MCP server 候选列表。
- 局域网扫描：按网段、端口和内置路径探测 WebSocket MCP server。
- 多设备连接：候选 server 以复选框列出，支持全选、连接和断开。
- 快捷联控：提供音频、头部、表情、时钟、设置菜单和字符显示等常用控制。
- 工具列表：连接后可发送 `tools/list`，合并显示所有 server 返回的工具 schema。
- 日志：显示连接、发送、接收、发现和错误信息。

## 安装依赖

```bash
cd mcp_gui_client
python3 -m pip install -r requirements.txt
```

`tkinter` 使用 Python 自带库；如果你的 Python 发行版没有内置 tkinter，需要按系统方式安装。

## 启动

```bash
cd mcp_gui_client
python3 app.py
```

## 使用流程

1. 打开后进入 `连接与控制` 页。
2. 等待 UDP 自动发现，或确认 `局域网搜索` 中的网段、端口和超时后点击 `搜索`。
3. 搜索或发现到的 server 会显示在左侧候选列表中，候选项默认勾选。
4. 按需点击 `全选` 或单独勾选设备。
5. 点击 `连接`，客户端会连接所选 server。
6. 连接成功后客户端会自动发送 `initialize`，随后可以使用右侧快捷控制区广播命令。
7. 在 `工具列表` 页点击 `列出工具`，可以查看各 server 返回的工具 schema 和自动生成的参数 JSON。
8. 在 `日志` 页可以查看 WebSocket 收发内容和错误信息，点击 `清空` 可清除日志。

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
  "version": "v4.0.0"
}
```

### 主动扫描

`连接与控制` 页的 `局域网搜索` 支持配置：

- `网段`：默认使用本机所在网段，例如 `192.168.1.0/24`。
- `端口`：默认 `8765,8000,8080,9000`，支持逗号或分号分隔。
- `超时秒`：默认 `0.6` 秒。

扫描范围最多支持 1024 个 host。客户端会对开放 TCP 端口继续探测内置 WebSocket 路径：`/mcp`、`/`。

## 快捷控制

`连接与控制` 页右侧的快捷控制会调用以下 MCP 工具，并广播到所有已连接 server：

- 音频：`self.audio_speaker.set_volume`、`self.audio_speaker.set_mute`、`self.audio_speaker.play_sound`
- 头部：`self.head.turn`、`self.head.center`、`self.head.nod`、`self.head.shake`、`self.head.roll`
- 表情：`self.emoji.play_gif`、`self.emoji.random_gif`、`self.emoji.set_eye`
- 模式：`self.clock.show_brief`、`self.clock.start_mode`、`self.clock.stop_mode`、`self.settings_menu.start_mode`、`self.settings_menu.stop_mode`
- 字符：`self.oled.show_code`

其中 `退出时钟模式` 会先调用 `self.clock.stop_mode`，再调用 `self.emoji.set_eye` 恢复当前选择的眼睛表情。

## 消息格式

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
