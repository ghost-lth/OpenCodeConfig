import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lsp_tcp_proxy


class FakeStdin:
    def __init__(self, on_write):
        self.buffer = b""
        self._on_write = on_write
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer += data
        self._on_write(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self._on_write)
        self._wait_event = asyncio.Event()
        self.terminated = False

    def _on_write(self, data: bytes) -> None:
        self.stdout.feed_data(b"echo:" + data)
        self.stdout.feed_eof()

    def terminate(self) -> None:
        self.terminated = True
        self._wait_event.set()

    def kill(self) -> None:
        self.terminated = True
        self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return 0


@pytest.mark.asyncio
async def test_proxy_handles_single_client(monkeypatch):
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        proc = FakeProcess()
        processes.append(proc)
        return proc

    monkeypatch.setattr(
        lsp_tcp_proxy.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    proxy = lsp_tcp_proxy.LspTcpProxy("fake", [], "127.0.0.1", 0, "per-client")
    server = await asyncio.start_server(proxy._handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]

    reader, writer = await asyncio.open_connection(host, port)
    writer.write(b"hello")
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()

    server.close()
    await server.wait_closed()

    assert response == b"echo:hello"
    assert len(processes) == 1
    assert processes[0].stdin.buffer == b"hello"


@pytest.mark.asyncio
async def test_proxy_allows_multiple_clients(monkeypatch):
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        proc = FakeProcess()
        processes.append(proc)
        return proc

    monkeypatch.setattr(
        lsp_tcp_proxy.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    proxy = lsp_tcp_proxy.LspTcpProxy("fake", [], "127.0.0.1", 0, "per-client")
    server = await asyncio.start_server(proxy._handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]

    async def run_client(payload: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(payload)
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data

    results = await asyncio.gather(
        run_client(b"one"),
        run_client(b"two"),
    )

    server.close()
    await server.wait_closed()

    assert sorted(results) == [b"echo:one", b"echo:two"]
    assert len(processes) == 2
    assert sorted(proc.stdin.buffer for proc in processes) == [b"one", b"two"]


@pytest.mark.asyncio
async def test_proxy_shared_mode_reuses_process(monkeypatch):
    processes = []

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        proc = FakeProcess()
        processes.append(proc)
        return proc

    monkeypatch.setattr(
        lsp_tcp_proxy.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    proxy = lsp_tcp_proxy.LspTcpProxy("fake", [], "127.0.0.1", 0, "shared")
    server = await asyncio.start_server(proxy._handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]

    async def run_client(payload: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(payload)
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data

    first = await run_client(b"one")
    second = await run_client(b"two")

    server.close()
    await server.wait_closed()

    assert first == b"echo:one"
    assert second == b"echo:two"
    assert len(processes) == 1
