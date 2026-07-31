#!/usr/bin/env python3
"""Desk Emoji WebSocket MCP group-control client."""

from __future__ import annotations

import ipaddress
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable
from urllib.parse import urlparse

import customtkinter as ctk
from PIL import Image

from desk_emoji_sdk import APP_VERSION
from desk_emoji_sdk import ServerCandidate as SdkServerCandidate
from desk_emoji_sdk import discover_servers, receive_udp_announcements
from help_content import configure_help_tags, load_help_markdown, render_help_markdown


JSONDict = dict[str, Any]
COPYRIGHT_URL = "https://ewen.ltd"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")
READONLY_TEXT_SHORTCUT_MOD_MASK = 0x04 | 0x10 | 0x40 | 0x80
READONLY_TEXT_NAV_KEYS = {
    "Left",
    "Right",
    "Up",
    "Down",
    "Home",
    "End",
    "Prior",
    "Next",
    "Escape",
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Command",
    "Meta_L",
    "Meta_R",
    "Super_L",
    "Super_R",
}


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def example_arguments(tool: JSONDict) -> JSONDict:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set(properties)

    arguments: JSONDict = {}
    for name, definition in properties.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            continue
        if name not in required_names and "default" not in definition:
            continue
        if "default" in definition:
            arguments[name] = definition["default"]
            continue
        prop_type = definition.get("type")
        if prop_type == "boolean":
            arguments[name] = False
        elif prop_type == "integer":
            minimum = definition.get("minimum")
            arguments[name] = minimum if isinstance(minimum, int) else 0
        elif prop_type == "string":
            arguments[name] = ""
        else:
            arguments[name] = None
    return arguments


def local_subnet() -> str:
    ip = "192.168.1.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        pass
    finally:
        sock.close()
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0/24"
    return "192.168.1.0/24"


def parse_ports(text: str) -> list[int]:
    ports: list[int] = []
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        port = int(item)
        if port < 1 or port > 65535:
            raise ValueError(f"Invalid port: {port}")
        ports.append(port)
    return ports


def parse_paths(text: str) -> list[str]:
    paths: list[str] = []
    for item in text.replace(";", ",").split(","):
        item = item.strip() or "/"
        if not item.startswith("/"):
            item = "/" + item
        paths.append(item)
    return paths


def validate_ota_url(url: str) -> str | None:
    if not url:
        return "请输入 OTA URL"
    if len(url) < 10 or len(url) > 512:
        return "OTA 地址长度必须在 10-512 个字符之间"
    if any(ch.isspace() for ch in url):
        return "OTA 地址不能包含空白字符"

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "OTA 地址必须使用 https 协议"
    if not parsed.netloc or not parsed.hostname:
        return "OTA 地址必须包含有效主机名"

    try:
        port = parsed.port
    except ValueError:
        return "OTA 地址端口号不正确"
    if port is not None and (port < 1 or port > 65535):
        return "OTA 地址端口号必须在 1-65535 之间"

    return None


def load_websocket_module():
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install websocket-client") from exc
    return websocket


def gui_candidate(candidate: SdkServerCandidate) -> "ServerCandidate":
    return ServerCandidate(
        url=candidate.url,
        label=candidate.url,
        server_info=candidate.version,
    )


def is_user_tool(tool: JSONDict) -> bool:
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        return False
    audience = annotations.get("audience")
    return isinstance(audience, list) and "user" in audience


@dataclass(frozen=True)
class ServerCandidate:
    url: str
    label: str
    server_info: str = ""


class WebSocketConnection:
    def __init__(
        self,
        url: str,
        session_id: str,
        token: str,
        on_message: Callable[[str, JSONDict], None],
        on_log: Callable[[str], None],
    ):
        self.url = url
        self.session_id = session_id
        self.token = token
        self.on_message = on_message
        self.on_log = on_log
        self.app = None
        self.thread: threading.Thread | None = None
        self.open = False
        self._send_lock = threading.Lock()

    def connect(self) -> None:
        websocket = load_websocket_module()
        headers = ["Protocol-Version: 1"]
        if self.token:
            token = self.token
            if not token.lower().startswith("bearer "):
                token = "Bearer " + token
            headers.append("Authorization: " + token)

        self.app = websocket.WebSocketApp(
            self.url,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=lambda ws, err: self.on_log(f"{self.url} error: {err}"),
            on_close=self._on_close,
        )
        self.thread = threading.Thread(target=self.app.run_forever, daemon=True)
        self.thread.start()

    def _on_open(self, ws) -> None:
        self.open = True
        self.on_log(f"{self.url} connected")
        hello = {"type": "hello", "transport": "websocket", "session_id": self.session_id}
        ws.send(json.dumps(hello, ensure_ascii=False, separators=(",", ":")))
        self.on_log(f">> {self.url} {pretty_json(hello)}")

    def _on_message(self, ws, raw: str) -> None:
        self.on_log(f"<< {self.url} {raw}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.on_log(f"{self.url} invalid JSON: {exc}")
            return
        if isinstance(value, dict):
            self.on_message(self.url, value)

    def _on_close(self, ws, code, reason) -> None:
        self.open = False
        self.on_log(f"{self.url} closed: {code} {reason}")

    def send_payload(self, payload: JSONDict) -> None:
        if self.app is None or not self.open:
            raise RuntimeError(f"{self.url} is not connected")
        envelope = {"session_id": self.session_id, "type": "mcp", "payload": payload}
        raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        with self._send_lock:
            self.app.send(raw)
        self.on_log(f">> {self.url} {raw}")

    def disconnect(self) -> None:
        self.open = False
        if self.app is not None:
            self.app.close()
            self.app = None


class McpGui(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title(f"Desk-Emoji MCP 客户端 {APP_VERSION}")
        window_size = (1040, 980)
        self.geometry(f"{window_size[0]}x{window_size[1]}")
        self.minsize(*window_size)
        self.icon_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "icons")
        self._load_icons()

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.next_id = 1
        self.candidates: list[ServerCandidate] = []
        self.candidate_vars: dict[str, tk.BooleanVar] = {}
        self.connections: dict[str, WebSocketConnection] = {}
        self.tools: dict[str, JSONDict] = {}
        self.tool_buttons: dict[str, ctk.CTkButton] = {}
        self.selected_tool_name: str | None = None

        self._build_vars()
        self._build_ui()
        self.start_udp_discovery()
        self.after(100, self._drain_queue)

    def _load_icons(self) -> None:
        try:
            self.iconphoto(True, tk.PhotoImage(file=os.path.join(self.icon_path, "main_icon.png")))
        except Exception:
            pass

        self.logo_image = self._load_single_icon("main_icon.png", size=(26, 26))
        self.control_icon = self._load_theme_icon("cpu", size=(20, 20))
        self.tools_icon = self._load_theme_icon("tool", size=(20, 20))
        self.log_icon = self._load_theme_icon("log", size=(20, 20))
        self.help_icon = self._load_theme_icon("help", size=(20, 20))

    def _load_single_icon(self, filename: str, size: tuple[int, int]) -> ctk.CTkImage | None:
        try:
            return ctk.CTkImage(Image.open(os.path.join(self.icon_path, filename)), size=size)
        except Exception:
            return None

    def _load_theme_icon(self, name: str, size: tuple[int, int]) -> ctk.CTkImage | None:
        try:
            return ctk.CTkImage(
                light_image=Image.open(os.path.join(self.icon_path, f"{name}_dark.png")),
                dark_image=Image.open(os.path.join(self.icon_path, f"{name}_light.png")),
                size=size,
            )
        except Exception:
            return None

    def _build_vars(self) -> None:
        self.subnet_var = tk.StringVar(value=local_subnet())
        self.ports_var = tk.StringVar(value="8765")
        self.paths_var = tk.StringVar(value="/mcp")
        self.timeout_var = tk.DoubleVar(value=1.2)
        self.session_var = tk.StringVar(value=f"session-{int(time.time())}")
        self.token_var = tk.StringVar()
        self.select_all_candidates_var = tk.BooleanVar(value=False)
        self.show_user_tools_only_var = tk.BooleanVar(value=False)

        self.volume_var = tk.IntVar(value=100)
        self.mute_var = tk.BooleanVar(value=False)
        self.sound_var = tk.StringVar(value="success")
        self.direction_var = tk.StringVar(value="left")
        self.offset_var = tk.IntVar(value=10)
        self.x_var = tk.IntVar(value=0)
        self.y_var = tk.IntVar(value=0)
        self.hold_var = tk.IntVar(value=0)
        self.loop_var = tk.IntVar(value=3)
        self.gif_name_var = tk.StringVar(value="rocket")
        self.frame_delay_var = tk.IntVar(value=10)
        self.eye_var = tk.StringVar(value="blink")
        self.code_var = tk.StringVar(value="")
        self.ota_url_var = tk.StringVar(value="")
        self.text_input: Any | None = None
        self.gesture_var = tk.BooleanVar(value=True)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_navigation()
        self._build_control_page()
        self._build_tools_page()
        self._build_log_page()
        self._build_help_page()
        self.select_frame_by_name("control")

    def _build_navigation(self) -> None:
        self.navigation_frame = ctk.CTkFrame(self, corner_radius=0)
        self.navigation_frame.grid(row=0, column=0, sticky="nsew")
        self.navigation_frame.grid_columnconfigure(0, weight=1)
        self.navigation_frame.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            self.navigation_frame,
            text="  Desk-Emoji",
            image=self.logo_image,
            compound="left",
            font=ctk.CTkFont(size=16, weight="bold"),
            justify="left",
        ).grid(row=0, column=0, padx=20, pady=20, sticky="w")

        self.control_button = self._nav_button("控制", "control", self.control_icon)
        self.control_button.grid(row=1, column=0, sticky="ew")
        self.tools_button = self._nav_button("工具", "tools", self.tools_icon)
        self.tools_button.grid(row=2, column=0, sticky="ew")
        self.log_button = self._nav_button("日志", "log", self.log_icon)
        self.log_button.grid(row=3, column=0, sticky="ew")
        self.help_button = self._nav_button("帮助", "help", self.help_icon)
        self.help_button.grid(row=4, column=0, sticky="ew")

        footer = ctk.CTkFrame(self.navigation_frame, fg_color="transparent")
        footer.grid(row=6, column=0, padx=5, pady=8, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(
            footer,
            values=["System", "Light", "Dark"],
            command=ctk.set_appearance_mode,
        ).grid(row=0, column=0, padx=8, pady=12, sticky="ew")

    def _nav_button(self, text: str, name: str, image: ctk.CTkImage | None = None) -> ctk.CTkButton:
        return ctk.CTkButton(
            self.navigation_frame,
            corner_radius=0,
            height=40,
            border_spacing=10,
            text=text,
            image=image,
            fg_color="transparent",
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray30"),
            anchor="w",
            command=lambda: self.select_frame_by_name(name),
        )

    def select_frame_by_name(self, name: str) -> None:
        selected = ("gray75", "gray25")
        for button_name, button in (
            ("control", self.control_button),
            ("tools", self.tools_button),
            ("log", self.log_button),
            ("help", self.help_button),
        ):
            button.configure(fg_color=selected if button_name == name else "transparent")

        for frame in (self.control_frame, self.tools_frame, self.log_frame, self.help_frame):
            frame.grid_forget()

        frame = {
            "control": self.control_frame,
            "tools": self.tools_frame,
            "log": self.log_frame,
            "help": self.help_frame,
        }[name]
        if name == "help":
            self.reload_help_page()
        frame.grid(row=0, column=1, sticky="nsew")

    def _build_control_page(self) -> None:
        self.control_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.control_frame.grid_columnconfigure(0, weight=1, uniform="control")
        self.control_frame.grid_columnconfigure(1, weight=1, uniform="control")
        self.control_frame.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        left.grid(row=0, column=0, padx=(20, 10), pady=20, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        form = self._section(left, "局域网搜索")
        form.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        form.columnconfigure(1, weight=1)
        ctk.CTkLabel(form, text="网段").grid(row=1, column=0, padx=12, pady=(4, 8), sticky="w")
        ctk.CTkEntry(form, textvariable=self.subnet_var).grid(row=1, column=1, padx=(0, 12), pady=(4, 8), sticky="ew")
        ctk.CTkLabel(form, text="端口").grid(row=2, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkEntry(form, textvariable=self.ports_var).grid(row=2, column=1, padx=(0, 12), pady=8, sticky="ew")
        ctk.CTkLabel(form, text="路径").grid(row=3, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkEntry(form, textvariable=self.paths_var).grid(row=3, column=1, padx=(0, 12), pady=8, sticky="ew")
        ctk.CTkLabel(form, text="超时秒").grid(row=4, column=0, padx=12, pady=(8, 12), sticky="w")
        ctk.CTkEntry(form, textvariable=self.timeout_var, width=90).grid(row=4, column=1, padx=(0, 12), pady=(8, 12), sticky="w")

        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(row=1, column=0, padx=0, pady=(0, 10), sticky="ew")
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        ctk.CTkButton(actions, text="搜索", command=self.start_discovery).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(actions, text="断开", command=self.disconnect_all).grid(row=0, column=1, padx=6, sticky="ew")
        ctk.CTkButton(actions, text="连接", command=self.connect_selected).grid(row=0, column=2, padx=(6, 0), sticky="ew")

        list_frame = self._section(left, "")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(2, weight=1)
        header = ctk.CTkFrame(list_frame, fg_color="transparent")
        header.grid(row=1, column=0, padx=12, pady=(12, 8), sticky="ew")
        header.columnconfigure(1, weight=1)
        ctk.CTkCheckBox(
            header,
            text="全选",
            variable=self.select_all_candidates_var,
            command=self.toggle_all_candidates,
        ).grid(row=0, column=0, sticky="w")
        self.status_label = ctk.CTkLabel(header, text="未连接", anchor="e")
        self.status_label.grid(row=0, column=1, sticky="e")

        self.candidate_inner = ctk.CTkScrollableFrame(list_frame)
        self.candidate_inner.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.candidate_inner.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        right.grid(row=0, column=1, padx=(10, 20), pady=20, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        self._build_quick_controls(right)

    def _section(self, parent: Any, title: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent)
        frame.grid_columnconfigure(0, weight=1)
        if title:
            ctk.CTkLabel(
                frame,
                text=title,
                anchor="w",
                font=ctk.CTkFont(size=15, weight="bold"),
            ).grid(row=0, column=0, columnspan=3, padx=12, pady=(12, 4), sticky="ew")
        return frame

    def _build_quick_controls(self, tab: Any) -> None:
        tab.columnconfigure(0, weight=1)

        audio = self._section(tab, "声音")
        audio.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        self.configure_equal_columns(audio)
        ctk.CTkLabel(audio, text="音量").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkSlider(audio, from_=0, to=100, variable=self.volume_var).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(audio, text="设置音量", command=lambda: self.call_tool_all("self.audio_speaker.set_volume", {"volume": self.volume_var.get()})).grid(row=1, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkCheckBox(audio, text="静音", variable=self.mute_var, command=lambda: self.call_tool_all("self.audio_speaker.set_mute", {"mute": self.mute_var.get()})).grid(row=2, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkComboBox(audio, variable=self.sound_var, values=["activation", "err_pin", "err_reg", "exclamation", "low_battery", "popup", "success", "upgrade", "vibration", "welcome", "wificonfig"], state="readonly").grid(row=2, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(audio, text="播放提示音", command=lambda: self.call_tool_all("self.audio_speaker.play_sound", {"name": self.sound_var.get()})).grid(row=2, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(audio, text="开始聆听", command=lambda: self.call_tool_all("self.voice.start_listening", {})).grid(row=3, column=1, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(audio, text="停止聆听", command=lambda: self.call_tool_all("self.voice.stop_listening", {})).grid(row=3, column=2, padx=12, pady=8, sticky="ew")

        head = self._section(tab, "头部")
        head.grid(row=2, column=0, padx=0, pady=(10, 0), sticky="ew")
        self.configure_equal_columns(head)
        ctk.CTkLabel(head, text="方向").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkComboBox(head, variable=self.direction_var, values=["left", "right", "up", "down"], state="readonly").grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkLabel(head, text="偏移").grid(row=2, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkEntry(head, textvariable=self.offset_var).grid(row=2, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(head, text="居中", command=lambda: self.call_tool_all("self.head.center", {})).grid(row=3, column=0, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(head, text="转向", command=lambda: self.call_tool_all("self.head.turn", {"direction": self.direction_var.get(), "offset": self.offset_var.get()})).grid(row=3, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(head, text="点头", command=lambda: self.call_tool_all("self.head.nod", {"loop_count": self.loop_var.get()})).grid(row=3, column=2, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(head, text="摇头", command=lambda: self.call_tool_all("self.head.shake", {"loop_count": self.loop_var.get()})).grid(row=4, column=0, padx=8, pady=(0, 12), sticky="ew")
        ctk.CTkButton(head, text="左滚", command=lambda: self.call_tool_all("self.head.roll", {"direction": "left"})).grid(row=4, column=1, padx=8, pady=(0, 12), sticky="ew")
        ctk.CTkButton(head, text="右滚", command=lambda: self.call_tool_all("self.head.roll", {"direction": "right"})).grid(row=4, column=2, padx=8, pady=(0, 12), sticky="ew")

        emoji = self._section(tab, "表情")
        emoji.grid(row=1, column=0, padx=0, pady=(10, 0), sticky="ew")
        self.configure_equal_columns(emoji)
        ctk.CTkLabel(emoji, text="GIF").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkEntry(emoji, textvariable=self.gif_name_var).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(emoji, text="播放 GIF", command=self.play_gif).grid(row=1, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkLabel(emoji, text="循环/延迟/保持").grid(row=2, column=0, padx=12, pady=8, sticky="w")
        gif_opts = ctk.CTkFrame(emoji, fg_color="transparent")
        gif_opts.grid(row=2, column=1, padx=8, pady=8, sticky="ew")
        for column in range(3):
            gif_opts.grid_columnconfigure(column, weight=1)
        ctk.CTkEntry(gif_opts, textvariable=self.loop_var, width=54).grid(row=0, column=0, padx=(0, 4), sticky="ew")
        ctk.CTkEntry(gif_opts, textvariable=self.frame_delay_var, width=54).grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkEntry(gif_opts, textvariable=self.hold_var, width=54).grid(row=0, column=2, padx=(4, 0), sticky="ew")
        ctk.CTkButton(emoji, text="随机 GIF", command=lambda: self.call_tool_all("self.emoji.random_gif", self.gif_options())).grid(row=2, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkLabel(emoji, text="眼睛").grid(row=3, column=0, padx=12, pady=(8, 12), sticky="w")
        ctk.CTkComboBox(emoji, variable=self.eye_var, values=["blink", "happy", "sad", "angry", "surprised"], state="readonly").grid(row=3, column=1, padx=8, pady=(8, 12), sticky="ew")
        ctk.CTkButton(emoji, text="显示", command=lambda: self.call_tool_all("self.emoji.set_eye", {"expression": self.eye_var.get(), "hold_sec": self.hold_var.get()})).grid(row=3, column=2, padx=12, pady=(8, 12), sticky="ew")

        system = self._section(tab, "模式")
        system.grid(row=3, column=0, padx=0, pady=(10, 0), sticky="ew")
        self.configure_equal_columns(system)
        ctk.CTkButton(system, text="显示时钟", command=lambda: self.call_tool_all("self.clock.show_brief", {})).grid(row=1, column=0, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(system, text="开启时钟模式", command=lambda: self.call_tool_all("self.clock.start_mode", {})).grid(row=1, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkButton(system, text="退出时钟模式", command=self.exit_clock_mode).grid(row=1, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(system, text="打开设置菜单", command=lambda: self.call_tool_all("self.settings_menu.start_mode", {})).grid(row=2, column=1, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(system, text="关闭设置菜单", command=lambda: self.call_tool_all("self.settings_menu.stop_mode", {})).grid(row=2, column=2, padx=12, pady=8, sticky="ew")
        ctk.CTkLabel(system, text="回车显示字符").grid(row=3, column=0, padx=12, pady=(8, 12), sticky="w")
        code_validate = (self.register(self._validate_code), "%P")
        self.code_entry = ctk.CTkEntry(system, textvariable=self.code_var, validate="key", validatecommand=code_validate)
        self.code_entry.grid(row=3, column=1, padx=8, pady=(8, 12), sticky="ew")
        self.code_entry.bind("<Return>", self.show_code)
        ctk.CTkButton(system, text="关闭显示字符", command=self.clear_code).grid(row=3, column=2, padx=12, pady=(8, 12), sticky="ew")

        ota = self._section(tab, "OTA")
        ota.grid(row=4, column=0, padx=0, pady=(10, 0), sticky="ew")
        self.configure_equal_columns(ota)
        ctk.CTkLabel(ota, text="自定义地址").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        ota_entry = ctk.CTkEntry(ota, textvariable=self.ota_url_var)
        ota_entry.grid(row=1, column=1, columnspan=2, padx=12, pady=8, sticky="ew")
        ota_entry.bind("<Return>", lambda event: self.set_ota_url())
        ctk.CTkButton(ota, text="设置 OTA 地址", command=self.set_ota_url).grid(row=2, column=1, padx=12, pady=(8, 12), sticky="ew")
        ctk.CTkButton(ota, text="恢复默认 OTA", command=self.reset_ota_url).grid(row=2, column=2, padx=12, pady=(8, 12), sticky="ew")

    def _validate_code(self, value: str) -> bool:
        return len(value) <= 10

    def configure_equal_columns(self, frame: Any) -> None:
        for column in range(3):
            frame.columnconfigure(column, weight=1, uniform="quick_controls")

    def configure_readonly_textbox(self, textbox: Any) -> None:
        inner = getattr(textbox, "_textbox", textbox)
        inner.configure(state="normal")
        inner.bind("<KeyPress>", self._readonly_textbox_keypress, add="+")
        inner.bind("<<Paste>>", self._block_textbox_edit, add="+")
        inner.bind("<<Cut>>", self._block_textbox_edit, add="+")
        inner.bind("<<Clear>>", self._block_textbox_edit, add="+")
        inner.bind("<Control-a>", self._select_all_textbox, add="+")
        inner.bind("<Command-a>", self._select_all_textbox, add="+")
        inner.bind("<Control-c>", self._copy_textbox_selection, add="+")
        inner.bind("<Command-c>", self._copy_textbox_selection, add="+")

    def _readonly_textbox_keypress(self, event: tk.Event) -> str | None:
        if event.keysym in READONLY_TEXT_NAV_KEYS:
            return None
        if event.keysym.lower() in {"a", "c"} and event.state & READONLY_TEXT_SHORTCUT_MOD_MASK:
            return None
        return "break"

    def _block_textbox_edit(self, event: tk.Event) -> str:
        return "break"

    def _select_all_textbox(self, event: tk.Event) -> str:
        event.widget.tag_add("sel", "1.0", "end-1c")
        event.widget.mark_set("insert", "1.0")
        event.widget.see("insert")
        return "break"

    def _copy_textbox_selection(self, event: tk.Event) -> str:
        event.widget.event_generate("<<Copy>>")
        return "break"

    def _build_tools_page(self) -> None:
        tab = self.tools_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab)
        left.grid(row=0, column=0, padx=(20, 10), pady=20, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(
            left,
            text="工具列表",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(12, 8), sticky="ew")
        self.tools_list_frame = ctk.CTkScrollableFrame(left)
        tools_options = ctk.CTkFrame(left, fg_color="transparent")
        tools_options.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")
        tools_options.columnconfigure(1, weight=1)
        ctk.CTkCheckBox(
            tools_options,
            text="用户工具",
            variable=self.show_user_tools_only_var,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(tools_options, text="列出工具", width=120, command=self.list_tools_all).grid(row=0, column=2, sticky="e")
        self.tools_list_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.tools_list_frame.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(tab)
        right.grid(row=0, column=1, padx=(10, 20), pady=20, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        ctk.CTkLabel(right, text="工具说明", anchor="w", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, padx=12, pady=(12, 8), sticky="w")
        self.schema_text = ctk.CTkTextbox(right, height=220, wrap="word")
        self.schema_text.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="nsew")
        self.configure_readonly_textbox(self.schema_text)
        ctk.CTkLabel(right, text="调用参数", anchor="w", font=ctk.CTkFont(size=15, weight="bold")).grid(row=2, column=0, padx=12, pady=(4, 8), sticky="w")
        self.arguments_text = ctk.CTkTextbox(right, height=160, wrap="word")
        self.arguments_text.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="nsew")
        tool_actions = ctk.CTkFrame(right, fg_color="transparent")
        tool_actions.grid(row=4, column=0, padx=12, pady=(0, 12), sticky="ew")
        tool_actions.columnconfigure(0, weight=1)
        ctk.CTkButton(tool_actions, text="重置参数", width=120, command=self.reset_selected_tool_arguments).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(tool_actions, text="调用工具", width=120, command=self.call_selected_tool).grid(row=0, column=1, padx=(8, 0), sticky="e")
        self.show_tools_overview()

    def _build_log_page(self) -> None:
        tab = self.log_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")
        top.columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="日志", anchor="w", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(top, text="清空", width=80, command=lambda: self.log_text.delete("1.0", "end")).grid(row=0, column=1, padx=(8, 0), sticky="e")
        self.log_text = ctk.CTkTextbox(tab, wrap="word")
        self.log_text.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.log_text.tag_config("timestamp", foreground="red")
        self.configure_readonly_textbox(self.log_text)

    def _build_help_page(self) -> None:
        tab = self.help_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.help_text = ctk.CTkTextbox(tab, wrap="word")
        self.help_text.grid(row=0, column=0, padx=20, pady=(20, 8), sticky="nsew")
        configure_help_tags(self.help_text)
        self.configure_readonly_textbox(self.help_text)
        self.reload_help_page()

        footer = ctk.CTkFrame(tab, fg_color="transparent")
        footer.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        copyright_label = ctk.CTkLabel(
            footer,
            text="杭州易问科技版权所有",
            cursor="hand2",
            text_color=("#0645ad", "#8ab4f8"),
        )
        copyright_label.grid(row=0, column=0, sticky="e")
        copyright_label.bind("<Button-1>", lambda event: self.open_copyright_link())

    def reload_help_page(self) -> None:
        render_help_markdown(self.help_text, load_help_markdown())
        self.help_text.configure(state="normal")

    def open_copyright_link(self) -> None:
        webbrowser.open_new_tab(COPYRIGHT_URL)

    def start_udp_discovery(self) -> None:
        thread = threading.Thread(target=self._udp_discovery_worker, daemon=True)
        thread.start()

    def _udp_discovery_worker(self) -> None:
        try:
            for candidate in receive_udp_announcements(timeout=None):
                self.event_queue.put(("candidate", gui_candidate(candidate)))
        except Exception as exc:
            self.event_queue.put(("log", f"UDP discovery stopped: {exc}"))

    def start_discovery(self) -> None:
        try:
            network = ipaddress.ip_network(self.subnet_var.get().strip(), strict=False)
            ports = parse_ports(self.ports_var.get())
            paths = parse_paths(self.paths_var.get())
            timeout = float(self.timeout_var.get())
        except Exception as exc:
            messagebox.showerror("搜索参数错误", str(exc))
            return

        hosts = list(network.hosts())
        if len(hosts) > 1024:
            messagebox.showerror("搜索范围过大", "请使用 /24 或更小的网段")
            return

        self.status_label.configure(text="搜索中...")
        self.log(f"Start discovery: {network} ports={ports} paths={paths}")
        thread = threading.Thread(target=self._discover_worker, args=(str(network), ports, paths, timeout), daemon=True)
        thread.start()

    def _discover_worker(self, subnet: str, ports: list[int], paths: list[str], timeout: float) -> None:
        try:
            candidates = discover_servers(
                subnet,
                ports=ports,
                paths=paths,
                token=self.token_var.get().strip(),
                timeout=timeout,
                max_workers=32,
                retry_timeout=max(2.0, timeout),
            )
        except RuntimeError as exc:
            self.event_queue.put(("error", str(exc)))
            self.event_queue.put(("scan_done", None))
            return
        except Exception as exc:
            self.event_queue.put(("error", str(exc)))
            self.event_queue.put(("scan_done", None))
            return
        for candidate in candidates:
            self.event_queue.put(("candidate", gui_candidate(candidate)))
        self.event_queue.put(("scan_done", None))

    def refresh_candidates(self) -> None:
        for child in self.candidate_inner.winfo_children():
            child.destroy()
        self.candidate_vars = {}
        if not self.candidates:
            ctk.CTkLabel(
                self.candidate_inner,
                text="等待 UDP 广播或手动搜索设备",
                text_color=("gray45", "gray65"),
                anchor="w",
            ).grid(row=0, column=0, padx=6, pady=6, sticky="ew")
            self.update_select_all_candidates_state()
            return
        for index, candidate in enumerate(self.candidates):
            var = tk.BooleanVar(value=True)
            self.candidate_vars[candidate.url] = var
            row = ctk.CTkFrame(
                self.candidate_inner,
                corner_radius=0,
                fg_color=("gray92", "gray18") if index % 2 == 0 else ("gray96", "gray14"),
            )
            row.grid(row=index, column=0, sticky="ew")
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkCheckBox(
                row,
                text=candidate.url,
                variable=var,
                command=self.update_select_all_candidates_state,
            ).grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.candidate_inner.columnconfigure(0, weight=1)
        self.update_select_all_candidates_state()

    def set_all_candidates(self, selected: bool) -> None:
        for var in self.candidate_vars.values():
            var.set(selected)
        self.select_all_candidates_var.set(selected if self.candidate_vars else False)

    def toggle_all_candidates(self) -> None:
        self.set_all_candidates(self.select_all_candidates_var.get())

    def update_select_all_candidates_state(self) -> None:
        self.select_all_candidates_var.set(bool(self.candidate_vars) and all(var.get() for var in self.candidate_vars.values()))

    def connect_selected(self) -> None:
        selected = [candidate for candidate in self.candidates if self.candidate_vars.get(candidate.url, tk.BooleanVar(value=False)).get()]
        if not selected:
            messagebox.showinfo("提示", "请先选择 MCP 设备")
            return
        self.disconnect_all()
        for candidate in selected:
            conn = WebSocketConnection(
                candidate.url,
                self.session_var.get().strip(),
                self.token_var.get().strip(),
                self.queue_message,
                self.log,
            )
            self.connections[candidate.url] = conn
            try:
                conn.connect()
            except Exception as exc:
                self.log(f"{candidate.url} connect failed: {exc}")
        self.status_label.configure(text=f"连接中: {len(self.connections)} 个")
        self.after(1200, self._after_group_connect)

    def _after_group_connect(self) -> None:
        count = sum(1 for conn in self.connections.values() if conn.open)
        self.status_label.configure(text=f"已连接: {count} 个 MCP 设备")
        if count:
            self.initialize_all()

    def disconnect_all(self) -> None:
        for conn in list(self.connections.values()):
            conn.disconnect()
        self.connections.clear()
        self.status_label.configure(text="未连接")

    def queue_message(self, url: str, message: JSONDict) -> None:
        self.event_queue.put(("message", (url, message)))

    def log(self, message: str) -> None:
        self.event_queue.put(("log", message))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, value = self.event_queue.get_nowait()
                if kind == "log":
                    self._append_log(value)
                elif kind == "message":
                    url, message = value
                    self._handle_incoming(url, message)
                elif kind == "candidate":
                    existing_index = next((idx for idx, candidate in enumerate(self.candidates) if candidate.url == value.url), None)
                    if existing_index is None:
                        self.candidates.append(value)
                        self.refresh_candidates()
                        self._append_log(f"Found MCP 设备: {value.label}")
                    elif self.candidates[existing_index].label != value.label:
                        self.candidates[existing_index] = value
                        self.refresh_candidates()
                        self._append_log(f"Updated MCP 设备: {value.label}")
                elif kind == "scan_done":
                    self.status_label.configure(text=f"搜索完成: {len(self.candidates)} 个")
                elif kind == "error":
                    messagebox.showerror("错误", value)
                    self._append_log(value)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        self.log_text.insert("end", f"[{timestamp}]", "timestamp")
        self.log_text.insert("end", f" {message}\n")
        self.log_text.see("end")
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, f"{time.strftime('%Y-%m-%d')}.log")
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(log_line)

    def _handle_incoming(self, url: str, envelope: JSONDict) -> None:
        if envelope.get("type") == "hello":
            self._append_log(f"Hello from {url}: {pretty_json(envelope)}")
            return
        payload = envelope.get("payload", envelope)
        if not isinstance(payload, dict):
            return
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            for tool in result["tools"]:
                if isinstance(tool, dict) and "name" in tool:
                    if self.show_user_tools_only_var.get() and not is_user_tool(tool):
                        continue
                    name = str(tool["name"])
                    existing_servers: list[str] = []
                    if name in self.tools:
                        previous_servers = self.tools[name].get("servers", [])
                        if isinstance(previous_servers, list):
                            existing_servers = [str(server) for server in previous_servers]
                    tool = dict(tool)
                    servers = existing_servers
                    if url not in servers:
                        servers.append(url)
                    tool["servers"] = servers
                    self.tools[name] = tool
            self.refresh_tools_list()
            next_cursor = result.get("nextCursor")
            if isinstance(next_cursor, str) and next_cursor:
                self.send_tools_list_page(url, next_cursor)
        else:
            self._append_log(f"Payload from {url}: {pretty_json(payload)}")

    def broadcast_payload(self, payload: JSONDict) -> None:
        open_connections = [conn for conn in self.connections.values() if conn.open]
        if not open_connections:
            raise RuntimeError("请先连接 MCP 设备")

        failures: list[str] = []
        max_workers = min(32, len(open_connections))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(conn.send_payload, payload): conn.url
                for conn in open_connections
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append(f"{url}: {exc}")
        if failures:
            raise RuntimeError("\n".join(failures))

    def next_request_id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def initialize_all(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"capabilities": {}},
            "id": self.next_request_id(),
        }
        self.safe_broadcast(payload)

    def list_tools_all(self) -> None:
        self.tools = {}
        self.refresh_tools_list()
        open_connections = [conn for conn in self.connections.values() if conn.open]
        if not open_connections:
            messagebox.showerror("发送失败", "请先连接 MCP 设备")
            return

        failures: list[str] = []
        max_workers = min(32, len(open_connections))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(conn.send_payload, self.tools_list_payload("")): conn.url
                for conn in open_connections
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append(f"{url}: {exc}")
        if failures:
            messagebox.showerror("发送失败", "\n".join(failures))
            self._append_log(f"List tools failed: {'; '.join(failures)}")

    def tools_list_payload(self, cursor: str) -> JSONDict:
        params: JSONDict = {"cursor": cursor}
        if self.show_user_tools_only_var.get():
            params["withUserTools"] = True
        return {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": params,
            "id": self.next_request_id(),
        }

    def send_tools_list_page(self, url: str, cursor: str) -> None:
        conn = self.connections.get(url)
        if conn is None or not conn.open:
            self._append_log(f"Skip next tools/list page for {url}: not connected")
            return
        try:
            conn.send_payload(self.tools_list_payload(cursor))
        except Exception as exc:
            self._append_log(f"Next tools/list page failed for {url}: {exc}")

    def call_tool_all(self, name: str, arguments: JSONDict) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": self.next_request_id(),
        }
        self.safe_broadcast(payload)

    def safe_broadcast(self, payload: JSONDict) -> None:
        try:
            self.broadcast_payload(payload)
        except Exception as exc:
            messagebox.showerror("发送失败", str(exc))
            self._append_log(f"Broadcast failed: {exc}")

    def gif_options(self) -> JSONDict:
        return {
            "loop_count": self.loop_var.get(),
            "frame_delay_ms": self.frame_delay_var.get(),
            "hold_sec": self.hold_var.get(),
        }

    def play_gif(self) -> None:
        args = {"name": self.gif_name_var.get().strip(), **self.gif_options()}
        self.call_tool_all("self.emoji.play_gif", args)

    def exit_clock_mode(self) -> None:
        self.call_tool_all("self.clock.stop_mode", {})
        self.call_tool_all("self.emoji.set_eye", {"expression": self.eye_var.get(), "hold_sec": self.hold_var.get()})

    def show_code(self, event=None) -> str:
        self.call_tool_all("self.oled.show_text", {"code": self.code_var.get()})
        return "break"

    def clear_code(self) -> None:
        self.call_tool_all("self.oled.clear_text", {})
        self.call_tool_all("self.emoji.set_eye", {"expression": "blink", "hold_sec": self.hold_var.get()})

    def set_ota_url(self) -> None:
        url = self.ota_url_var.get().strip()
        error = validate_ota_url(url)
        if error:
            messagebox.showerror("OTA 地址格式错误", error)
            return
        self.call_tool_all("self.ota.set_url", {"url": url})

    def reset_ota_url(self) -> None:
        self.call_tool_all("self.ota.reset_url", {})

    def refresh_tools_list(self) -> None:
        for child in self.tools_list_frame.winfo_children():
            child.destroy()
        self.tool_buttons = {}
        self.selected_tool_name = None
        self.show_tools_overview()
        for index, name in enumerate(sorted(self.tools)):
            servers = self.tools[name].get("servers", [])
            suffix = f" ({len(servers)} servers)" if isinstance(servers, list) else ""
            button = ctk.CTkButton(
                self.tools_list_frame,
                text=name + suffix,
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                command=lambda tool_name=name: self.on_tool_selected(tool_name),
            )
            button.grid(row=index, column=0, padx=4, pady=3, sticky="ew")
            self.tool_buttons[name] = button
        tool_scope = "user tools" if self.show_user_tools_only_var.get() else "tools"
        self._append_log(f"Loaded {len(self.tools)} unique {tool_scope}")

    def show_tools_overview(self) -> None:
        self.set_tool_description("")
        self.arguments_text.delete("1.0", "end")
        self.arguments_text.insert("1.0", "{}")

    def set_tool_description(self, text: str) -> None:
        self.schema_text.delete("1.0", "end")
        self.schema_text.insert("1.0", text)

    def on_tool_selected(self, name: str | None = None) -> None:
        if not name:
            return
        self.selected_tool_name = name
        tool = self.tools[name]
        self.update_tool_selection_styles()
        self.set_tool_description(pretty_json(tool))
        self.reset_selected_tool_arguments()

    def update_tool_selection_styles(self) -> None:
        for name, button in self.tool_buttons.items():
            selected = name == self.selected_tool_name
            button.configure(
                fg_color=("gray20", "gray85") if selected else "transparent",
                text_color=("gray95", "gray10") if selected else ("gray10", "gray90"),
                hover_color=("gray30", "gray75") if selected else ("gray75", "gray25"),
            )

    def reset_selected_tool_arguments(self) -> None:
        if not self.selected_tool_name:
            messagebox.showinfo("提示", "请先选择工具")
            return
        tool = self.tools[self.selected_tool_name]
        self.arguments_text.delete("1.0", "end")
        self.arguments_text.insert("1.0", pretty_json(example_arguments(tool)))

    def call_selected_tool(self) -> None:
        if not self.selected_tool_name:
            messagebox.showinfo("提示", "请先选择工具")
            return
        raw_arguments = self.arguments_text.get("1.0", "end").strip() or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            messagebox.showerror("Arguments JSON 格式错误", str(exc))
            return
        if not isinstance(arguments, dict):
            messagebox.showerror("Arguments JSON 格式错误", "Arguments JSON 必须是 JSON object")
            return
        self.call_tool_all(self.selected_tool_name, arguments)

def main() -> None:
    app = McpGui()
    app.mainloop()


if __name__ == "__main__":
    main()
