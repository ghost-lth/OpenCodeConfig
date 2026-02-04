from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types


logging.basicConfig(level=logging.WARNING)


class LspProtocolError(RuntimeError):
    pass


@dataclass
class LspClient:
    process: Optional[asyncio.subprocess.Process] = None
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None
    reader_task: Optional[asyncio.Task] = None
    stderr_task: Optional[asyncio.Task] = None
    next_id: int = 1
    pending: Dict[int, asyncio.Future] = field(default_factory=dict)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tcp_mode: bool = False

    async def start(
        self,
        command: str,
        args: list[str],
        root_uri: str,
        client_name: Optional[str],
        client_version: Optional[str],
        init_options: Optional[Dict[str, Any]],
        capabilities: Optional[Dict[str, Any]],
        workspace_folders: Optional[list[Dict[str, Any]]],
        trace: Optional[str],
        env: Optional[Dict[str, str]],
        timeout_ms: Optional[int],
        tcp_host: Optional[str],
        tcp_port: Optional[int],
    ) -> Dict[str, Any]:
        async with self.start_lock:
            if self.process is not None or self.writer is not None:
                return {"error": "LSP server already running"}

            host = tcp_host or os.getenv("LSP_TCP_HOST")
            port = tcp_port or int(os.getenv("LSP_TCP_PORT", "0")) or None
            if host and port:
                self.tcp_mode = True
                self.reader, self.writer = await asyncio.open_connection(host, port)
                self.reader_task = asyncio.create_task(self._read_loop())
            else:
                self.tcp_mode = False
                self.process = await asyncio.create_subprocess_exec(
                    command,
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )

                if not self.process.stdin or not self.process.stdout:
                    raise LspProtocolError("Failed to open LSP stdio streams")

                self.reader = self.process.stdout
                self.writer = self.process.stdin
                self.reader_task = asyncio.create_task(self._read_loop())
                if self.process.stderr:
                    self.stderr_task = asyncio.create_task(self._stderr_loop())

            params: Dict[str, Any] = {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": capabilities or {},
            }
            if client_name or client_version:
                params["clientInfo"] = {
                    "name": client_name or "opencode",
                    "version": client_version or "",
                }
            if init_options is not None:
                params["initializationOptions"] = init_options
            if workspace_folders is not None:
                params["workspaceFolders"] = workspace_folders
            if trace:
                params["trace"] = trace

            response = await self.request(
                "initialize",
                params,
                timeout_ms=timeout_ms,
            )
            await self.notify("initialized", {})
            return response

    async def stop(self) -> Dict[str, Any]:
        if self.process is None and self.writer is None:
            return {"error": "LSP server not running"}
        if self.tcp_mode:
            await self._disconnect()
        else:
            await self._terminate_process()
        return {"ok": True}

    async def shutdown(self, timeout_ms: Optional[int]) -> Dict[str, Any]:
        if self.process is None and self.writer is None:
            return {"error": "LSP server not running"}
        if self.tcp_mode:
            await self._disconnect()
            return {"ok": True}
        response = await self.request("shutdown", None, timeout_ms=timeout_ms)
        await self.notify("exit", None)
        await self._terminate_process()
        return response

    async def request(
        self, method: str, params: Optional[Dict[str, Any]], timeout_ms: Optional[int]
    ) -> Dict[str, Any]:
        if self.process is None and self.writer is None:
            return {"error": "LSP server not running"}

        request_id = self.next_id
        self.next_id += 1
        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending[request_id] = future
        await self._send(message)

        timeout = None if timeout_ms is None else timeout_ms / 1000
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending.pop(request_id, None)
            return {"error": f"LSP request timeout after {timeout_ms}ms"}
        return response

    async def notify(self, method: str, params: Optional[Dict[str, Any]]) -> None:
        if self.process is None and self.writer is None:
            raise LspProtocolError("LSP server not running")
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: Dict[str, Any]) -> None:
        if self.process is None and self.writer is None:
            raise LspProtocolError("LSP server not running")
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        async with self.write_lock:
            assert self.writer is not None
            self.writer.write(header + body)
            await self.writer.drain()

    async def _read_loop(self) -> None:
        if self.reader is None:
            return
        reader = self.reader
        try:
            while True:
                message = await self._read_message(reader)
                if message is None:
                    break
                if "id" in message:
                    request_id = message["id"]
                    future = self.pending.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(message)
                else:
                    logging.getLogger("lsp").debug("notification: %s", message)
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)
            self.pending.clear()

    async def _stderr_loop(self) -> None:
        assert self.process is not None
        assert self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            logging.getLogger("lsp").warning(
                "stderr: %s", line.decode("utf-8").rstrip()
            )

    async def _read_message(
        self, reader: asyncio.StreamReader
    ) -> Optional[Dict[str, Any]]:
        headers: Dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                return None
            text = line.decode("ascii", errors="ignore").strip()
            if text == "":
                break
            key, _, value = text.partition(":")
            headers[key.lower()] = value.strip()

        length_text = headers.get("content-length")
        if not length_text:
            raise LspProtocolError("Missing Content-Length header")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise LspProtocolError("Invalid Content-Length header") from exc

        body = await reader.readexactly(length)
        return json.loads(body.decode("utf-8"))

    async def _terminate_process(self) -> None:
        if self.reader_task:
            self.reader_task.cancel()
        if self.stderr_task:
            self.stderr_task.cancel()
        if self.writer:
            self.writer.close()
            if hasattr(self.writer, "wait_closed"):
                await self.writer.wait_closed()
        if self.process is not None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.process.kill()
        self.process = None
        self.reader = None
        self.writer = None
        self.tcp_mode = False
        self.reader_task = None
        self.stderr_task = None
        self.pending.clear()

    async def _disconnect(self) -> None:
        if self.reader_task:
            self.reader_task.cancel()
        if self.writer:
            self.writer.close()
            if hasattr(self.writer, "wait_closed"):
                await self.writer.wait_closed()
        self.reader = None
        self.writer = None
        self.reader_task = None
        self.stderr_task = None
        self.tcp_mode = False
        self.pending.clear()


CLIENT = LspClient()


def _parse_call(*args: Any, **kwargs: Any) -> tuple[str, Dict[str, Any]]:
    req = None
    tool_name = None
    arguments: Dict[str, Any] = {}
    if args and isinstance(args[0], types.CallToolRequest):
        req = args[0]
    elif len(args) >= 2 and isinstance(args[0], str):
        tool_name = args[0]
        if isinstance(args[1], dict):
            arguments = args[1]
    elif "req" in kwargs and isinstance(kwargs["req"], types.CallToolRequest):
        req = kwargs["req"]
    elif "name" in kwargs:
        tool_name = kwargs.get("name")
        if isinstance(kwargs.get("arguments"), dict):
            arguments = kwargs.get("arguments") or {}

    if req is not None:
        tool_name = req.params.name
        arguments = req.params.arguments or {}

    if not tool_name:
        raise ValueError("Missing tool name")
    return tool_name, arguments


def _tool_start_definition() -> types.Tool:
    return types.Tool(
        name="lsp_start",
        description="Start an LSP server and initialize it.",
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "LSP server command"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command args",
                },
                "root_uri": {"type": "string", "description": "Workspace root URI"},
                "client_name": {"type": "string", "description": "Client name"},
                "client_version": {
                    "type": "string",
                    "description": "Client version",
                },
                "init_options": {
                    "type": "object",
                    "description": "Initialization options",
                },
                "capabilities": {
                    "type": "object",
                    "description": "Client capabilities",
                },
                "workspace_folders": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Workspace folders",
                },
                "trace": {
                    "type": "string",
                    "description": "Trace level",
                },
                "env": {
                    "type": "object",
                    "description": "Extra environment variables",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Initialization timeout in ms",
                },
                "tcp_host": {
                    "type": "string",
                    "description": "Connect to an existing LSP over TCP",
                },
                "tcp_port": {
                    "type": "integer",
                    "description": "TCP port for existing LSP",
                },
            },
            "required": ["command", "root_uri"],
        },
    )


def _tool_request_definition() -> types.Tool:
    return types.Tool(
        name="lsp_request",
        description="Send an LSP request and return the response.",
        inputSchema={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "LSP method"},
                "params": {"type": "object", "description": "Request params"},
                "timeout_ms": {
                    "type": "integer",
                    "description": "Request timeout in ms",
                },
            },
            "required": ["method"],
        },
    )


def _tool_notify_definition() -> types.Tool:
    return types.Tool(
        name="lsp_notify",
        description="Send an LSP notification without waiting for a response.",
        inputSchema={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "LSP method"},
                "params": {"type": "object", "description": "Notification params"},
            },
            "required": ["method"],
        },
    )


def _tool_shutdown_definition() -> types.Tool:
    return types.Tool(
        name="lsp_shutdown",
        description="Shutdown the LSP server and exit.",
        inputSchema={
            "type": "object",
            "properties": {
                "timeout_ms": {
                    "type": "integer",
                    "description": "Shutdown timeout in ms",
                }
            },
        },
    )


def _tool_stop_definition() -> types.Tool:
    return types.Tool(
        name="lsp_stop",
        description="Terminate the LSP server without shutdown.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    )


def create_server() -> Server:
    server = Server("lsp", version="1.0.0")

    @server.list_tools()
    async def list_tools(_: types.ListToolsRequest) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                _tool_start_definition(),
                _tool_request_definition(),
                _tool_notify_definition(),
                _tool_shutdown_definition(),
                _tool_stop_definition(),
            ]
        )

    @server.call_tool()
    async def call_tool(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        tool_name, arguments = _parse_call(*args, **kwargs)

        if tool_name == "lsp_start":
            command = arguments.get("command")
            root_uri = arguments.get("root_uri")
            if not command or not root_uri:
                return {"error": "Missing command or root_uri"}
            args_list = arguments.get("args") or []
            return await CLIENT.start(
                command=str(command),
                args=[str(item) for item in args_list],
                root_uri=str(root_uri),
                client_name=arguments.get("client_name"),
                client_version=arguments.get("client_version"),
                init_options=arguments.get("init_options"),
                capabilities=arguments.get("capabilities"),
                workspace_folders=arguments.get("workspace_folders"),
                trace=arguments.get("trace"),
                env=arguments.get("env"),
                timeout_ms=arguments.get("timeout_ms"),
                tcp_host=arguments.get("tcp_host"),
                tcp_port=arguments.get("tcp_port"),
            )

        if tool_name == "lsp_request":
            method = arguments.get("method")
            if not method:
                return {"error": "Missing method"}
            return await CLIENT.request(
                method=str(method),
                params=arguments.get("params"),
                timeout_ms=arguments.get("timeout_ms"),
            )

        if tool_name == "lsp_notify":
            method = arguments.get("method")
            if not method:
                return {"error": "Missing method"}
            await CLIENT.notify(str(method), arguments.get("params"))
            return {"ok": True}

        if tool_name == "lsp_shutdown":
            return await CLIENT.shutdown(arguments.get("timeout_ms"))

        if tool_name == "lsp_stop":
            return await CLIENT.stop()

        raise ValueError(f"Unknown tool: {tool_name}")

    return server


async def run_server() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    anyio.run(run_server)


if __name__ == "__main__":
    main()
