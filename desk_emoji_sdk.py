#!/usr/bin/env python3
"""Python SDK for Desk-Emoji WebSocket MCP devices."""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


JSONDict = dict[str, Any]
APP_VERSION = "v4.3.1"
DEFAULT_DISCOVERY_PORT = 37654
DEFAULT_SCAN_PORTS = (8765, 8000, 8080, 9000)
DEFAULT_SCAN_PATHS = ("/mcp", "/", "/xiaozhi/v1/")
DEFAULT_FAST_SCAN_PORTS = (8765,)
DEFAULT_FAST_SCAN_PATHS = ("/mcp",)
DISCOVERY_QUERY_TYPE = "desk-emoji.mcp.discover"
DISCOVERY_ANNOUNCE_TYPE = "desk-emoji.mcp.announce"
RECENT_TTL_SEC = 600.0


def load_websocket_module():
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install websocket-client") from exc
    return websocket


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bearer_token(token: str) -> str:
    token = token.strip()
    if token and not token.lower().startswith("bearer "):
        return "Bearer " + token
    return token


def protocol_headers(token: str = "") -> list[str]:
    headers = ["Protocol-Version: 1"]
    auth = bearer_token(token)
    if auth:
        headers.append("Authorization: " + auth)
    return headers


def normalize_path(path: str) -> str:
    path = path.strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def tcp_open(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


@dataclass(frozen=True)
class ServerCandidate:
    url: str
    version: str = ""
    source: str = ""
    status: str = "online"
    last_seen: float = 0.0
    name: str = ""
    device_id: str = ""

    @property
    def label(self) -> str:
        parts = [self.url]
        if self.version:
            parts.append(f"version={self.version}")
        if self.source:
            parts.append(self.source)
        if self.status and self.status != "online":
            parts.append(self.status)
        return "  ".join(parts)


_recent_candidates: dict[str, ServerCandidate] = {}


def _now() -> float:
    return time.time()


def _touch_recent(candidate: ServerCandidate, *, status: str | None = None) -> ServerCandidate:
    updated = ServerCandidate(
        url=candidate.url,
        version=candidate.version,
        source=candidate.source,
        status=status or candidate.status or "online",
        last_seen=candidate.last_seen or _now(),
        name=candidate.name,
        device_id=candidate.device_id,
    )
    _recent_candidates[updated.url] = updated
    return updated


def _recent_snapshot(ttl_sec: float = RECENT_TTL_SEC) -> list[ServerCandidate]:
    cutoff = _now() - ttl_sec
    stale = [url for url, candidate in _recent_candidates.items() if candidate.last_seen and candidate.last_seen < cutoff]
    for url in stale:
        _recent_candidates.pop(url, None)
    return [
        ServerCandidate(
            url=candidate.url,
            version=candidate.version,
            source=candidate.source or "cache",
            status="recent",
            last_seen=candidate.last_seen,
            name=candidate.name,
            device_id=candidate.device_id,
        )
        for candidate in _recent_candidates.values()
    ]


def candidate_from_announcement(msg: JSONDict, addr_host: str = "", source: str = "UDP announce") -> ServerCandidate | None:
    if msg.get("type") != DISCOVERY_ANNOUNCE_TYPE or msg.get("transport") != "websocket":
        return None
    try:
        host = str(msg.get("ip") or addr_host)
        if not host:
            return None
        server_port = int(msg.get("port") or 8765)
        path = normalize_path(str(msg.get("path") or "/mcp"))
    except Exception:
        return None
    return ServerCandidate(
        url=f"ws://{host}:{server_port}{path}",
        version=str(msg.get("version") or ""),
        source=source,
        status="online",
        last_seen=_now(),
        name=str(msg.get("name") or ""),
        device_id=str(msg.get("id") or ""),
    )


class DeskEmojiClient:
    """Blocking client for one Desk-Emoji WebSocket MCP 设备."""

    def __init__(self, url: str, session_id: str = "", token: str = "", timeout: float = 5.0):
        self.url = url
        self.session_id = session_id or f"session-{int(time.time())}"
        self.token = token
        self.timeout = timeout
        self.ws = None
        self.next_id = 1
        self._send_lock = threading.Lock()

    def __enter__(self) -> "DeskEmojiClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        websocket = load_websocket_module()
        self.ws = websocket.create_connection(
            self.url,
            timeout=self.timeout,
            header=protocol_headers(self.token),
        )
        self.ws.settimeout(self.timeout)
        self.send_raw({"type": "hello", "transport": "websocket", "session_id": self.session_id})

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def send_raw(self, message: JSONDict) -> None:
        if self.ws is None:
            raise RuntimeError("Client is not connected")
        with self._send_lock:
            self.ws.send(dumps_json(message))

    def recv_raw(self) -> JSONDict:
        if self.ws is None:
            raise RuntimeError("Client is not connected")
        value = json.loads(self.ws.recv())
        if not isinstance(value, dict):
            raise RuntimeError("Expected JSON object from server")
        return value

    def request(self, method: str, params: JSONDict | None = None, request_id: int | None = None) -> JSONDict:
        actual_request_id = request_id if request_id is not None else self._next_request_id()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": actual_request_id,
        }
        self.send_raw({"session_id": self.session_id, "type": "mcp", "payload": payload})
        return self.recv_payload(request_id=actual_request_id)

    def request_nowait(self, method: str, params: JSONDict | None = None, request_id: int | None = None) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": request_id if request_id is not None else self._next_request_id(),
        }
        self.send_raw({"session_id": self.session_id, "type": "mcp", "payload": payload})

    def recv_payload(self, request_id: int | None = None) -> JSONDict:
        while True:
            envelope = self.recv_raw()
            if envelope.get("type") == "hello":
                continue
            payload = envelope.get("payload", envelope)
            if isinstance(payload, dict):
                if request_id is not None and payload.get("id") != request_id:
                    continue
                return payload

    def initialize(self) -> JSONDict:
        return self.request("initialize", {"capabilities": {}})

    def list_tools(self, with_user_tools: bool = False) -> list[JSONDict]:
        collected: list[JSONDict] = []
        cursor = ""
        while True:
            params: JSONDict = {"cursor": cursor}
            if with_user_tools:
                params["withUserTools"] = True
            payload = self.request("tools/list", params)
            result = payload.get("result", {})
            if not isinstance(result, dict):
                break
            tools = result.get("tools")
            if not isinstance(tools, list):
                break
            collected.extend(tool for tool in tools if isinstance(tool, dict))
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return collected

    def call_tool(self, name: str, arguments: JSONDict | None = None) -> JSONDict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def set_volume(self, volume: int) -> JSONDict:
        return self.call_tool("self.audio_speaker.set_volume", {"volume": volume})

    def set_mute(self, mute: bool) -> JSONDict:
        return self.call_tool("self.audio_speaker.set_mute", {"mute": mute})

    def play_sound(self, name: str) -> JSONDict:
        return self.call_tool("self.audio_speaker.play_sound", {"name": name})

    def start_listening(self) -> JSONDict:
        return self.call_tool("self.voice.start_listening")

    def stop_listening(self) -> JSONDict:
        return self.call_tool("self.voice.stop_listening")

    def turn_head(self, direction: str, offset: int = 10) -> JSONDict:
        return self.call_tool("self.head.turn", {"direction": direction, "offset": offset})

    def center_head(self) -> JSONDict:
        return self.call_tool("self.head.center")

    def nod_head(self, loop_count: int = 3) -> JSONDict:
        return self.call_tool("self.head.nod", {"loop_count": loop_count})

    def shake_head(self, loop_count: int = 3) -> JSONDict:
        return self.call_tool("self.head.shake", {"loop_count": loop_count})

    def roll_head(self, direction: str) -> JSONDict:
        return self.call_tool("self.head.roll", {"direction": direction})

    def play_gif(
        self,
        name: str,
        loop_count: int = 3,
        frame_delay_ms: int = 10,
        hold_sec: int = 0,
    ) -> JSONDict:
        return self.call_tool(
            "self.emoji.play_gif",
            {
                "name": name,
                "loop_count": loop_count,
                "frame_delay_ms": frame_delay_ms,
                "hold_sec": hold_sec,
            },
        )

    def random_gif(self, loop_count: int = 3, frame_delay_ms: int = 10, hold_sec: int = 0) -> JSONDict:
        return self.call_tool(
            "self.emoji.random_gif",
            {
                "loop_count": loop_count,
                "frame_delay_ms": frame_delay_ms,
                "hold_sec": hold_sec,
            },
        )

    def set_eye(self, expression: str = "blink", hold_sec: int = 0) -> JSONDict:
        return self.call_tool("self.emoji.set_eye", {"expression": expression, "hold_sec": hold_sec})

    def show_clock_brief(self) -> JSONDict:
        return self.call_tool("self.clock.show_brief")

    def start_clock_mode(self) -> JSONDict:
        return self.call_tool("self.clock.start_mode")

    def stop_clock_mode(self) -> JSONDict:
        return self.call_tool("self.clock.stop_mode")

    def start_settings_menu(self) -> JSONDict:
        return self.call_tool("self.settings_menu.start_mode")

    def stop_settings_menu(self) -> JSONDict:
        return self.call_tool("self.settings_menu.stop_mode")

    def show_text(self, code: str) -> JSONDict:
        return self.call_tool("self.oled.show_text", {"code": code})

    def clear_text(self) -> JSONDict:
        return self.call_tool("self.oled.clear_text")

    def set_ota_url(self, url: str) -> JSONDict:
        return self.call_tool("self.ota.set_url", {"url": url})

    def reset_ota_url(self) -> JSONDict:
        return self.call_tool("self.ota.reset_url")

    def _next_request_id(self) -> int:
        request_id = self.next_id
        self.next_id += 1
        return request_id


class DeskEmojiGroup:
    """Broadcast helper for multiple Desk-Emoji servers."""

    def __init__(self, urls: Iterable[str], session_id: str = "", token: str = "", timeout: float = 5.0):
        base_session = session_id or f"session-{int(time.time())}"
        self.clients = [
            DeskEmojiClient(url, session_id=base_session, token=token, timeout=timeout)
            for url in urls
        ]

    def __enter__(self) -> "DeskEmojiGroup":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        for client in self.clients:
            client.connect()

    def close(self) -> None:
        for client in self.clients:
            client.close()

    def initialize(self) -> dict[str, JSONDict]:
        return self.broadcast("initialize", {"capabilities": {}})

    def initialize_nowait(self) -> dict[str, str]:
        return self.broadcast_nowait("initialize", {"capabilities": {}})

    def call_tool(self, name: str, arguments: JSONDict | None = None) -> dict[str, JSONDict]:
        return self.broadcast("tools/call", {"name": name, "arguments": arguments or {}})

    def call_tool_nowait(self, name: str, arguments: JSONDict | None = None) -> dict[str, str]:
        return self.broadcast_nowait("tools/call", {"name": name, "arguments": arguments or {}})

    def set_volume(self, volume: int, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.audio_speaker.set_volume", {"volume": volume}, nowait)

    def set_mute(self, mute: bool, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.audio_speaker.set_mute", {"mute": mute}, nowait)

    def play_sound(self, name: str, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.audio_speaker.play_sound", {"name": name}, nowait)

    def start_listening(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.voice.start_listening", {}, nowait)

    def stop_listening(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.voice.stop_listening", {}, nowait)

    def turn_head(self, direction: str, offset: int = 10, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.head.turn", {"direction": direction, "offset": offset}, nowait)

    def center_head(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.head.center", {}, nowait)

    def nod_head(self, loop_count: int = 3, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.head.nod", {"loop_count": loop_count}, nowait)

    def shake_head(self, loop_count: int = 3, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.head.shake", {"loop_count": loop_count}, nowait)

    def roll_head(self, direction: str, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.head.roll", {"direction": direction}, nowait)

    def play_gif(
        self,
        name: str,
        loop_count: int = 3,
        frame_delay_ms: int = 10,
        hold_sec: int = 0,
        *,
        nowait: bool = True,
    ) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait(
            "self.emoji.play_gif",
            {
                "name": name,
                "loop_count": loop_count,
                "frame_delay_ms": frame_delay_ms,
                "hold_sec": hold_sec,
            },
            nowait,
        )

    def random_gif(
        self,
        loop_count: int = 3,
        frame_delay_ms: int = 10,
        hold_sec: int = 0,
        *,
        nowait: bool = True,
    ) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait(
            "self.emoji.random_gif",
            {
                "loop_count": loop_count,
                "frame_delay_ms": frame_delay_ms,
                "hold_sec": hold_sec,
            },
            nowait,
        )

    def set_eye(self, expression: str = "blink", hold_sec: int = 0, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.emoji.set_eye", {"expression": expression, "hold_sec": hold_sec}, nowait)

    def show_clock_brief(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.clock.show_brief", {}, nowait)

    def start_clock_mode(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.clock.start_mode", {}, nowait)

    def stop_clock_mode(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.clock.stop_mode", {}, nowait)

    def start_settings_menu(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.settings_menu.start_mode", {}, nowait)

    def stop_settings_menu(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.settings_menu.stop_mode", {}, nowait)

    def show_text(self, code: str, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.oled.show_text", {"code": code}, nowait)

    def clear_text(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.oled.clear_text", {}, nowait)

    def set_ota_url(self, url: str, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.ota.set_url", {"url": url}, nowait)

    def reset_ota_url(self, *, nowait: bool = True) -> dict[str, JSONDict] | dict[str, str]:
        return self._call_tool_maybe_nowait("self.ota.reset_url", {}, nowait)

    def _call_tool_maybe_nowait(self, name: str, arguments: JSONDict, nowait: bool) -> dict[str, JSONDict] | dict[str, str]:
        if nowait:
            return self.call_tool_nowait(name, arguments)
        return self.call_tool(name, arguments)

    def broadcast(self, method: str, params: JSONDict | None = None) -> dict[str, JSONDict]:
        responses: dict[str, JSONDict] = {}
        for client in self.clients:
            responses[client.url] = client.request(method, params or {})
        return responses

    def broadcast_nowait(self, method: str, params: JSONDict | None = None) -> dict[str, str]:
        if not self.clients:
            return {}

        request_id = max(client.next_id for client in self.clients)
        for client in self.clients:
            client.next_id = max(client.next_id, request_id + 1)

        results: dict[str, str] = {}
        max_workers = min(32, len(self.clients))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(client.request_nowait, method, params or {}, request_id): client.url
                for client in self.clients
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    future.result()
                    results[url] = "sent"
                except Exception as exc:
                    results[url] = f"error: {exc}"
        return results


def probe_server(
    url: str,
    session_id: str = "",
    token: str = "",
    timeout: float = 1.2,
    source: str = "scan fast",
) -> ServerCandidate | None:
    try:
        with DeskEmojiClient(url, session_id=session_id, token=token, timeout=timeout) as client:
            response = client.initialize()
        version = ""
        result = response.get("result")
        if isinstance(result, dict):
            server_info = result.get("serverInfo")
            if isinstance(server_info, dict):
                version = str(server_info.get("version") or "")
        return _touch_recent(ServerCandidate(url=url, version=version, source=source, status="online", last_seen=_now()))
    except Exception:
        return None


def _host_port_from_url(url: str) -> tuple[str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or not parsed.port:
        return None
    return parsed.hostname, parsed.port


def scan_servers(
    subnet: str,
    ports: Iterable[int] = DEFAULT_SCAN_PORTS,
    paths: Iterable[str] = DEFAULT_SCAN_PATHS,
    token: str = "",
    timeout: float = 1.2,
    max_hosts: int = 1024,
    max_workers: int = 32,
    retry_timeout: float = 2.0,
) -> list[ServerCandidate]:
    network = ipaddress.ip_network(subnet, strict=False)
    hosts = list(network.hosts())
    if len(hosts) > max_hosts:
        raise ValueError(f"Scan range is too large: {len(hosts)} hosts > {max_hosts}")

    normalized_paths = [normalize_path(path) for path in paths]
    found: dict[str, ServerCandidate] = {}

    def scan_port(host: str, port: int) -> ServerCandidate | None:
        if not tcp_open(host, port, timeout):
            return None
        for path in normalized_paths:
            url = f"ws://{host}:{port}{path}"
            candidate = probe_server(url, token=token, timeout=timeout, source="scan fast")
            if candidate is None and retry_timeout > timeout:
                candidate = probe_server(url, token=token, timeout=retry_timeout, source="scan retry")
            if candidate is not None:
                return candidate
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(scan_port, str(host), int(port))
            for host in hosts
            for port in ports
        ]
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                found[candidate.url] = _touch_recent(candidate)
    return list(found.values())


def send_udp_discovery_query(
    port: int = DEFAULT_DISCOVERY_PORT,
    timeout: float = 0.2,
    broadcast_hosts: Iterable[str] = ("255.255.255.255",),
) -> None:
    payload = dumps_json({"type": DISCOVERY_QUERY_TYPE, "transport": "websocket", "version": 1}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for host in broadcast_hosts:
            try:
                sock.sendto(payload, (host, port))
            except OSError:
                continue
    finally:
        sock.close()


def query_udp_announcements(
    port: int = DEFAULT_DISCOVERY_PORT,
    listen_timeout: float = 1.2,
    bind_host: str = "",
    bind_port: int = 0,
    broadcast_hosts: Iterable[str] = ("255.255.255.255",),
) -> list[ServerCandidate]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    found: dict[str, ServerCandidate] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind((bind_host, bind_port))
        except OSError:
            return []
        payload = dumps_json({"type": DISCOVERY_QUERY_TYPE, "transport": "websocket", "version": 1}).encode("utf-8")
        for host in broadcast_hosts:
            try:
                sock.sendto(payload, (host, port))
            except OSError:
                continue

        deadline = _now() + listen_timeout
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                raw, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            candidate = candidate_from_announcement(msg, addr[0], source="UDP reply")
            if candidate is not None:
                found[candidate.url] = _touch_recent(candidate)
    finally:
        sock.close()
    return list(found.values())


def discover_servers(
    subnet: str,
    ports: Iterable[int] = DEFAULT_FAST_SCAN_PORTS,
    paths: Iterable[str] = DEFAULT_FAST_SCAN_PATHS,
    token: str = "",
    timeout: float = 1.2,
    max_hosts: int = 1024,
    max_workers: int = 32,
    retry_timeout: float = 2.0,
    udp_timeout: float = 1.2,
    include_cache: bool = True,
    recent_ttl_sec: float = RECENT_TTL_SEC,
) -> list[ServerCandidate]:
    found: dict[str, ServerCandidate] = {}

    for candidate in _recent_snapshot(recent_ttl_sec) if include_cache else []:
        found[candidate.url] = candidate

    for candidate in query_udp_announcements(listen_timeout=udp_timeout):
        found[candidate.url] = candidate

    verify_hosts: set[tuple[str, int]] = set()
    for candidate in list(found.values()):
        target = _host_port_from_url(candidate.url)
        if target is not None:
            verify_hosts.add(target)

    normalized_paths = [normalize_path(path) for path in paths]
    for host, port in verify_hosts:
        for path in normalized_paths:
            url = f"ws://{host}:{port}{path}"
            candidate = probe_server(url, token=token, timeout=timeout, source="UDP verify")
            if candidate is None and retry_timeout > timeout:
                candidate = probe_server(url, token=token, timeout=retry_timeout, source="scan retry")
            if candidate is not None:
                found[candidate.url] = candidate
                break

    for candidate in scan_servers(
        subnet,
        ports=ports,
        paths=paths,
        token=token,
        timeout=timeout,
        max_hosts=max_hosts,
        max_workers=max_workers,
        retry_timeout=retry_timeout,
    ):
        found[candidate.url] = candidate

    return sorted(found.values(), key=lambda item: (item.status != "online", item.url))


def receive_udp_announcements(
    bind_host: str = "",
    port: int = DEFAULT_DISCOVERY_PORT,
    timeout: float | None = None,
):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        sock.settimeout(timeout)
        while True:
            raw, addr = sock.recvfrom(4096)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            candidate = candidate_from_announcement(msg, addr[0], source="UDP announce")
            if candidate is None:
                continue
            yield _touch_recent(candidate)
    finally:
        sock.close()
