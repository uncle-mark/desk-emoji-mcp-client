#!/usr/bin/env python3
"""Python SDK for Desk-Emoji WebSocket MCP devices."""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable


JSONDict = dict[str, Any]
APP_VERSION = "v4.1.0"
DEFAULT_DISCOVERY_PORT = 37654
DEFAULT_SCAN_PORTS = (8765, 8000, 8080, 9000)
DEFAULT_SCAN_PATHS = ("/mcp", "/", "/xiaozhi/v1/")


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

    @property
    def label(self) -> str:
        parts = [self.url]
        if self.version:
            parts.append(f"version={self.version}")
        if self.source:
            parts.append(self.source)
        return "  ".join(parts)


class DeskEmojiClient:
    """Blocking client for one Desk-Emoji WebSocket MCP server."""

    def __init__(self, url: str, session_id: str = "", token: str = "", timeout: float = 5.0):
        self.url = url
        self.session_id = session_id or f"session-{int(time.time())}"
        self.token = token
        self.timeout = timeout
        self.ws = None
        self.next_id = 1

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
        self.ws.send(dumps_json(message))

    def recv_raw(self) -> JSONDict:
        if self.ws is None:
            raise RuntimeError("Client is not connected")
        value = json.loads(self.ws.recv())
        if not isinstance(value, dict):
            raise RuntimeError("Expected JSON object from server")
        return value

    def request(self, method: str, params: JSONDict | None = None, request_id: int | None = None) -> JSONDict:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": request_id if request_id is not None else self._next_request_id(),
        }
        self.send_raw({"session_id": self.session_id, "type": "mcp", "payload": payload})
        return self.recv_payload()

    def recv_payload(self) -> JSONDict:
        while True:
            envelope = self.recv_raw()
            if envelope.get("type") == "hello":
                continue
            payload = envelope.get("payload", envelope)
            if isinstance(payload, dict):
                return payload

    def initialize(self) -> JSONDict:
        return self.request("initialize", {"capabilities": {}})

    def list_tools(self) -> list[JSONDict]:
        payload = self.request("tools/list", {"cursor": ""})
        result = payload.get("result", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: JSONDict | None = None) -> JSONDict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

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

    def call_tool(self, name: str, arguments: JSONDict | None = None) -> dict[str, JSONDict]:
        return self.broadcast("tools/call", {"name": name, "arguments": arguments or {}})

    def broadcast(self, method: str, params: JSONDict | None = None) -> dict[str, JSONDict]:
        responses: dict[str, JSONDict] = {}
        for client in self.clients:
            responses[client.url] = client.request(method, params or {})
        return responses


def probe_server(url: str, session_id: str = "", token: str = "", timeout: float = 0.6) -> ServerCandidate | None:
    try:
        with DeskEmojiClient(url, session_id=session_id, token=token, timeout=timeout) as client:
            response = client.initialize()
        version = ""
        result = response.get("result")
        if isinstance(result, dict):
            server_info = result.get("serverInfo")
            if isinstance(server_info, dict):
                version = str(server_info.get("version") or "")
        return ServerCandidate(url=url, version=version)
    except Exception:
        return None


def scan_servers(
    subnet: str,
    ports: Iterable[int] = DEFAULT_SCAN_PORTS,
    paths: Iterable[str] = DEFAULT_SCAN_PATHS,
    token: str = "",
    timeout: float = 0.6,
    max_hosts: int = 1024,
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
            candidate = probe_server(url, token=token, timeout=timeout)
            if candidate is not None:
                return candidate
        return None

    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = [
            executor.submit(scan_port, str(host), int(port))
            for host in hosts
            for port in ports
        ]
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                found[candidate.url] = candidate
    return list(found.values())


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
            if msg.get("type") != "desk-emoji.mcp.announce" or msg.get("transport") != "websocket":
                continue
            host = str(msg.get("ip") or addr[0])
            server_port = int(msg.get("port") or 8765)
            path = normalize_path(str(msg.get("path") or "/mcp"))
            version = str(msg.get("version") or "")
            yield ServerCandidate(
                url=f"ws://{host}:{server_port}{path}",
                version=version,
                source="UDP",
            )
    finally:
        sock.close()
