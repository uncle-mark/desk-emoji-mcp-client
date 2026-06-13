#!/usr/bin/env python3
"""Desk Emoji WebSocket MCP group-control client."""

from __future__ import annotations

import ipaddress
import json
import queue
import socket
import threading
import time
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable

from desk_emoji_sdk import APP_VERSION


JSONDict = dict[str, Any]
COPYRIGHT_URL = "https://ewen.ltd"


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def parse_json_object(text: str) -> JSONDict:
    value = json.loads(text.strip() or "{}")
    if not isinstance(value, dict):
        raise ValueError("JSON must be an object")
    return value


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


def tcp_open(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def load_websocket_module():
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install websocket-client") from exc
    return websocket


def format_candidate_label(url: str, version: str = "", source: str = "") -> str:
    parts = [url]
    if version:
        parts.append(f"version={version}")
    if source:
        parts.append(source)
    return "  ".join(parts)


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
        self.app.send(raw)
        self.on_log(f">> {self.url} {raw}")

    def disconnect(self) -> None:
        self.open = False
        if self.app is not None:
            self.app.close()
            self.app = None


class McpGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Desk-Emoji MCP Client {APP_VERSION}")
        self.geometry("1180x780")
        self.minsize(980, 650)

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.next_id = 1
        self.candidates: list[ServerCandidate] = []
        self.candidate_vars: dict[str, tk.BooleanVar] = {}
        self.connections: dict[str, WebSocketConnection] = {}
        self.tools: dict[str, JSONDict] = {}
        self.udp_discovery_seen: set[str] = set()

        self._build_vars()
        self._build_ui()
        self.start_udp_discovery()
        self.after(100, self._drain_queue)

    def _build_vars(self) -> None:
        self.subnet_var = tk.StringVar(value=local_subnet())
        self.ports_var = tk.StringVar(value="8765,8000,8080,9000")
        self.paths_var = tk.StringVar(value="/mcp,/,/xiaozhi/v1/")
        self.timeout_var = tk.DoubleVar(value=0.6)
        self.session_var = tk.StringVar(value=f"session-{int(time.time())}")
        self.token_var = tk.StringVar()
        self.select_all_candidates_var = tk.BooleanVar(value=False)

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
        self.gesture_var = tk.BooleanVar(value=True)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_control_tab()
        self._build_tools_tab()
        self._build_log_tab()

        status = ttk.Frame(self)
        status.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        status.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status, text="未连接")
        self.status_label.grid(row=0, column=0, sticky="w")
        copyright_label = tk.Label(
            status,
            text="杭州易问科技版权所有",
            fg="#0645ad",
            cursor="hand2",
        )
        copyright_label.grid(row=0, column=1, sticky="e")
        copyright_label.bind("<Button-1>", lambda event: self.open_copyright_link())

    def _build_control_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="连接与控制")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)

        left = ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(left, text="局域网搜索", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="网段").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.subnet_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(form, text="端口").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.ports_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Label(form, text="超时秒").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(form, from_=0.2, to=3.0, increment=0.1, textvariable=self.timeout_var, width=8).grid(row=2, column=1, sticky="w", padx=8)

        actions = ttk.Frame(left)
        actions.grid(row=1, column=0, sticky="ew", pady=10)
        for column in range(2):
            actions.columnconfigure(column, weight=1)
        ttk.Button(actions, text="搜索", command=self.start_discovery).grid(row=0, column=0, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(actions, text="连接", command=self.connect_selected).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(actions, text="断开", command=self.disconnect_all).grid(row=0, column=2, sticky="ew", padx=(4, 0), pady=(4, 0))

        select_all_check = ttk.Checkbutton(
            left,
            text="全选",
            variable=self.select_all_candidates_var,
            command=self.toggle_all_candidates,
        )
        list_frame = ttk.LabelFrame(left, labelwidget=select_all_check, padding=8)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.candidate_canvas = tk.Canvas(list_frame, highlightthickness=0)
        self.candidate_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.candidate_canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.candidate_canvas.configure(yscrollcommand=scroll.set)
        self.candidate_inner = ttk.Frame(self.candidate_canvas)
        self.candidate_window = self.candidate_canvas.create_window((0, 0), window=self.candidate_inner, anchor="nw")
        self.candidate_inner.bind("<Configure>", lambda e: self.candidate_canvas.configure(scrollregion=self.candidate_canvas.bbox("all")))
        self.candidate_canvas.bind("<Configure>", lambda e: self.candidate_canvas.itemconfigure(self.candidate_window, width=e.width))

        right_canvas = tk.Canvas(tab, highlightthickness=0)
        right_canvas.grid(row=0, column=1, sticky="nsew")
        right_scroll = ttk.Scrollbar(tab, orient="vertical", command=right_canvas.yview)
        right_scroll.grid(row=0, column=2, sticky="ns")
        right_canvas.configure(yscrollcommand=right_scroll.set)
        right = ttk.Frame(right_canvas)
        right_window = right_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.bind("<Configure>", lambda e: right_canvas.itemconfigure(right_window, width=e.width))
        self._build_quick_controls(right)

    def _build_quick_controls(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)

        audio = ttk.LabelFrame(tab, text="音频", padding=10)
        audio.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        self.configure_equal_columns(audio)
        ttk.Label(audio, text="音量").grid(row=0, column=0, sticky="w")
        ttk.Scale(audio, from_=0, to=100, variable=self.volume_var, orient="horizontal").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(audio, text="设置音量", command=lambda: self.call_tool_all("self.audio_speaker.set_volume", {"volume": self.volume_var.get()})).grid(row=0, column=2)
        ttk.Checkbutton(audio, text="静音", variable=self.mute_var, command=lambda: self.call_tool_all("self.audio_speaker.set_mute", {"mute": self.mute_var.get()})).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(audio, textvariable=self.sound_var, values=["activation", "err_pin", "err_reg", "exclamation", "low_battery", "popup", "success", "upgrade", "vibration", "welcome", "wificonfig"], state="readonly").grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(audio, text="播放提示音", command=lambda: self.call_tool_all("self.audio_speaker.play_sound", {"name": self.sound_var.get()})).grid(row=1, column=2)

        head = ttk.LabelFrame(tab, text="头部", padding=10)
        head.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        self.configure_equal_columns(head)
        ttk.Label(head, text="方向").grid(row=0, column=0, sticky="w")
        ttk.Combobox(head, textvariable=self.direction_var, values=["left", "right", "up", "down"], state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(head, text="偏移").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(head, from_=1, to=20, textvariable=self.offset_var, width=8).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(head, text="转向", command=lambda: self.call_tool_all("self.head.turn", {"direction": self.direction_var.get(), "offset": self.offset_var.get()})).grid(row=1, column=2)
        ttk.Button(head, text="居中", command=lambda: self.call_tool_all("self.head.center", {})).grid(row=2, column=0, pady=4, sticky="ew")
        ttk.Button(head, text="点头", command=lambda: self.call_tool_all("self.head.nod", {"loop_count": self.loop_var.get()})).grid(row=2, column=1, pady=4, sticky="ew")
        ttk.Button(head, text="摇头", command=lambda: self.call_tool_all("self.head.shake", {"loop_count": self.loop_var.get()})).grid(row=2, column=2, pady=4, sticky="ew")
        ttk.Button(head, text="左滚", command=lambda: self.call_tool_all("self.head.roll", {"direction": "left"})).grid(row=5, column=1, sticky="ew")
        ttk.Button(head, text="右滚", command=lambda: self.call_tool_all("self.head.roll", {"direction": "right"})).grid(row=5, column=2, sticky="ew")

        emoji = ttk.LabelFrame(tab, text="表情", padding=10)
        emoji.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.configure_equal_columns(emoji)
        ttk.Label(emoji, text="GIF").grid(row=0, column=0, sticky="w")
        ttk.Entry(emoji, textvariable=self.gif_name_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(emoji, text="播放 GIF", command=self.play_gif).grid(row=0, column=2)
        ttk.Label(emoji, text="循环/延迟/保持").grid(row=1, column=0, sticky="w")
        gif_opts = ttk.Frame(emoji)
        gif_opts.grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Spinbox(gif_opts, from_=1, to=10, textvariable=self.loop_var, width=5).pack(side="left")
        ttk.Spinbox(gif_opts, from_=1, to=100, textvariable=self.frame_delay_var, width=5).pack(side="left", padx=4)
        ttk.Spinbox(gif_opts, from_=0, to=10, textvariable=self.hold_var, width=5).pack(side="left")
        ttk.Button(emoji, text="随机 GIF", command=lambda: self.call_tool_all("self.emoji.random_gif", self.gif_options())).grid(row=1, column=2)
        ttk.Label(emoji, text="眼睛").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(emoji, textvariable=self.eye_var, values=["blink", "happy", "sad", "angry", "surprised"], state="readonly").grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Button(emoji, text="显示", command=lambda: self.call_tool_all("self.emoji.set_eye", {"expression": self.eye_var.get(), "hold_sec": self.hold_var.get()})).grid(row=2, column=2)

        system = ttk.LabelFrame(tab, text="模式", padding=10)
        system.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        self.configure_equal_columns(system)
        ttk.Button(system, text="显示时钟", command=lambda: self.call_tool_all("self.clock.show_brief", {})).grid(row=0, column=0, sticky="ew", pady=3)
        ttk.Button(system, text="开启时钟模式", command=lambda: self.call_tool_all("self.clock.start_mode", {})).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(system, text="退出时钟模式", command=self.exit_clock_mode).grid(row=0, column=2, sticky="ew", pady=3)
        ttk.Button(system, text="打开设置菜单", command=lambda: self.call_tool_all("self.settings_menu.start_mode", {})).grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Button(system, text="关闭设置菜单", command=lambda: self.call_tool_all("self.settings_menu.stop_mode", {})).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(system, text="显示字符（最多10个）").grid(row=2, column=0, sticky="w", pady=4)
        code_validate = (self.register(self._validate_code), "%P")
        self.code_entry = ttk.Entry(system, textvariable=self.code_var, validate="key", validatecommand=code_validate)
        self.code_entry.grid(row=2, column=1, sticky="ew", padx=8)
        self.code_entry.bind("<Return>", self.show_code)

    def _validate_code(self, value: str) -> bool:
        return len(value) <= 10

    def configure_equal_columns(self, frame: ttk.Frame) -> None:
        for column in range(3):
            frame.columnconfigure(column, weight=1, uniform="quick_controls")

    def _build_tools_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="工具列表")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)

        self.tools_listbox = tk.Listbox(tab, exportselection=False)
        self.tools_listbox.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.tools_listbox.bind("<<ListboxSelect>>", self.on_tool_selected)

        right = ttk.Frame(tab)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        ttk.Label(right, text="Schema").grid(row=0, column=0, sticky="w")
        self.schema_text = tk.Text(right, height=12, wrap="word")
        self.schema_text.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
        ttk.Label(right, text="Arguments JSON").grid(row=2, column=0, sticky="w")
        self.args_text = tk.Text(right, height=10, wrap="word")
        self.args_text.grid(row=3, column=0, sticky="nsew", pady=(2, 8))
        tool_actions = ttk.Frame(right)
        tool_actions.grid(row=4, column=0, sticky="e")
        ttk.Button(tool_actions, text="列出工具", command=self.list_tools_all).pack(side="left", padx=(0, 8))

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="日志")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Button(tab, text="清空", command=lambda: self.log_text.delete("1.0", "end")).grid(row=0, column=0, sticky="e", pady=(0, 8))
        self.log_text = tk.Text(tab, wrap="word")
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.tag_configure("timestamp", foreground="red")
        scroll = ttk.Scrollbar(tab, command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def open_copyright_link(self) -> None:
        webbrowser.open_new_tab(COPYRIGHT_URL)

    def start_udp_discovery(self) -> None:
        thread = threading.Thread(target=self._udp_discovery_worker, daemon=True)
        thread.start()

    def _udp_discovery_worker(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", 37654))
            while True:
                raw, addr = sock.recvfrom(4096)
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "desk-emoji.mcp.announce":
                    continue
                if msg.get("transport") != "websocket":
                    continue
                ip = str(msg.get("ip") or addr[0])
                port = int(msg.get("port") or 8765)
                path = str(msg.get("path") or "/mcp")
                if not path.startswith("/"):
                    path = "/" + path
                url = f"ws://{ip}:{port}{path}"
                if url in self.udp_discovery_seen:
                    continue
                self.udp_discovery_seen.add(url)
                version = str(msg.get("version") or "")
                label = format_candidate_label(url, version, "UDP")
                self.event_queue.put(("candidate", ServerCandidate(url=url, label=label, server_info=version)))
        except Exception as exc:
            self.event_queue.put(("log", f"UDP discovery stopped: {exc}"))
        finally:
            sock.close()

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

        self.candidates = []
        self.refresh_candidates()
        self.status_label.configure(text="搜索中...")
        self.log(f"Start discovery: {network} ports={ports} paths={paths}")
        thread = threading.Thread(target=self._discover_worker, args=(hosts, ports, paths, timeout), daemon=True)
        thread.start()

    def _discover_worker(self, hosts: list[ipaddress.IPv4Address | ipaddress.IPv6Address], ports: list[int], paths: list[str], timeout: float) -> None:
        websocket = None
        try:
            websocket = load_websocket_module()
        except RuntimeError as exc:
            self.event_queue.put(("error", str(exc)))
            self.event_queue.put(("scan_done", None))
            return

        found: dict[str, ServerCandidate] = {}
        headers = ["Protocol-Version: 1"]
        token = self.token_var.get().strip()
        if token:
            if not token.lower().startswith("bearer "):
                token = "Bearer " + token
            headers.append("Authorization: " + token)

        def scan_port(host: str, port: int) -> ServerCandidate | None:
            if not tcp_open(host, port, timeout):
                return None
            for path in paths:
                url = f"ws://{host}:{port}{path}"
                candidate = self._probe_websocket(websocket, url, headers, timeout)
                if candidate is not None:
                    return candidate
            return None

        futures = []
        with ThreadPoolExecutor(max_workers=64) as executor:
            for host_obj in hosts:
                host = str(host_obj)
                for port in ports:
                    futures.append(executor.submit(scan_port, host, port))
            for future in as_completed(futures):
                candidate = future.result()
                if candidate is not None and candidate.url not in found:
                    found[candidate.url] = candidate
                    self.event_queue.put(("candidate", candidate))
        self.event_queue.put(("scan_done", None))

    def _probe_websocket(self, websocket, url: str, headers: list[str], timeout: float) -> ServerCandidate | None:
        try:
            ws = websocket.create_connection(url, timeout=timeout, header=headers)
            ws.settimeout(timeout)
            session_id = self.session_var.get().strip()
            hello = {"type": "hello", "transport": "websocket", "session_id": session_id}
            ws.send(json.dumps(hello, ensure_ascii=False, separators=(",", ":")))
            payload = {
                "session_id": session_id,
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                    "id": 1,
                },
            }
            ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            version = ""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if isinstance(msg, dict):
                    body = msg.get("payload", msg)
                    if isinstance(body, dict):
                        result = body.get("result")
                        if isinstance(result, dict) and "serverInfo" in result:
                            server_info = result["serverInfo"]
                            if isinstance(server_info, dict):
                                version = str(server_info.get("version") or "")
                            break
                    if msg.get("type") == "hello":
                        break
            ws.close()
            label = format_candidate_label(url, version)
            return ServerCandidate(url=url, label=label, server_info=version)
        except Exception:
            return None

    def refresh_candidates(self) -> None:
        for child in self.candidate_inner.winfo_children():
            child.destroy()
        self.candidate_vars = {}
        for index, candidate in enumerate(self.candidates):
            var = tk.BooleanVar(value=True)
            self.candidate_vars[candidate.url] = var
            ttk.Checkbutton(
                self.candidate_inner,
                text=candidate.label,
                variable=var,
                command=self.update_select_all_candidates_state,
            ).grid(row=index, column=0, sticky="ew", pady=2)
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
            messagebox.showinfo("提示", "请先选择 MCP server")
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
        self.status_label.configure(text=f"已连接: {count} 个 MCP server")
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
                    if not any(candidate.url == value.url for candidate in self.candidates):
                        self.candidates.append(value)
                        self.refresh_candidates()
                        self._append_log(f"Found MCP server: {value.label}")
                elif kind == "scan_done":
                    self.status_label.configure(text=f"搜索完成: {len(self.candidates)} 个")
                elif kind == "error":
                    messagebox.showerror("错误", value)
                    self._append_log(value)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}]", "timestamp")
        self.log_text.insert("end", f" {message}\n")
        self.log_text.see("end")

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
        else:
            self._append_log(f"Payload from {url}: {pretty_json(payload)}")

    def broadcast_payload(self, payload: JSONDict) -> None:
        open_connections = [conn for conn in self.connections.values() if conn.open]
        if not open_connections:
            raise RuntimeError("没有已连接的 MCP server")
        failures: list[str] = []
        for conn in open_connections:
            try:
                conn.send_payload(payload)
            except Exception as exc:
                failures.append(f"{conn.url}: {exc}")
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
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {"cursor": ""},
            "id": self.next_request_id(),
        }
        self.safe_broadcast(payload)

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
        self.call_tool_all("self.oled.show_code", {"code": self.code_var.get()})
        return "break"

    def refresh_tools_list(self) -> None:
        self.tools_listbox.delete(0, "end")
        for name in sorted(self.tools):
            servers = self.tools[name].get("servers", [])
            suffix = f" ({len(servers)} servers)" if isinstance(servers, list) else ""
            self.tools_listbox.insert("end", name + suffix)
        self._append_log(f"Loaded {len(self.tools)} unique tools")

    def selected_tool_name(self) -> str | None:
        selection = self.tools_listbox.curselection()
        if not selection:
            return None
        text = self.tools_listbox.get(selection[0])
        return text.split(" (", 1)[0]

    def on_tool_selected(self, event=None) -> None:
        name = self.selected_tool_name()
        if not name:
            return
        tool = self.tools[name]
        self.schema_text.delete("1.0", "end")
        self.schema_text.insert("1.0", pretty_json(tool))
        self.args_text.delete("1.0", "end")
        self.args_text.insert("1.0", pretty_json(self.default_args_for_tool(tool)))

    def default_args_for_tool(self, tool: JSONDict) -> JSONDict:
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        args: JSONDict = {}
        if isinstance(props, dict):
            for name, spec in props.items():
                if not isinstance(spec, dict):
                    continue
                if "default" in spec:
                    args[name] = spec["default"]
                elif spec.get("type") == "integer":
                    args[name] = spec.get("minimum", 0)
                elif spec.get("type") == "boolean":
                    args[name] = False
                else:
                    args[name] = ""
        return args

    def call_selected_tool(self) -> None:
        name = self.selected_tool_name()
        if not name:
            messagebox.showinfo("提示", "请先选择工具")
            return
        try:
            args = parse_json_object(self.args_text.get("1.0", "end"))
        except Exception as exc:
            messagebox.showerror("参数 JSON 错误", str(exc))
            return
        self.call_tool_all(name, args)

def main() -> None:
    app = McpGui()
    app.mainloop()


if __name__ == "__main__":
    main()
