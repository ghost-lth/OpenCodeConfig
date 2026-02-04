from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional


logging.basicConfig(level=logging.WARNING)


class LspTcpProxy:
    def __init__(
        self,
        command: str,
        args: list[str],
        host: str,
        port: int,
        mode: str,
    ) -> None:
        self.command = command
        self.args = args
        self.host = host
        self.port = port
        self.mode = mode
        self.process: Optional[asyncio.subprocess.Process] = None
        self.client_active = False
        self.process_lock = asyncio.Lock()

    async def run(self) -> None:
        if self.mode == "shared":
            await self._ensure_process()
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        addr = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
        logging.getLogger("lsp").warning("LSP TCP proxy listening on %s", addr)
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.mode == "shared":
            if self.client_active:
                writer.write(b"Proxy busy\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
            self.client_active = True
            process = await self._ensure_process()
            if not process.stdin or not process.stdout:
                self.client_active = False
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("Failed to open LSP stdio streams")
            try:
                to_lsp = asyncio.create_task(self._pipe(reader, process.stdin))
                from_lsp = asyncio.create_task(self._pipe(process.stdout, writer))
                done, pending = await asyncio.wait(
                    [to_lsp, from_lsp], return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
            finally:
                writer.close()
                await writer.wait_closed()
                self.client_active = False
            return

        process = await self._start_process()
        if not process.stdin or not process.stdout:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("Failed to open LSP stdio streams")
        try:
            to_lsp = asyncio.create_task(self._pipe(reader, process.stdin))
            from_lsp = asyncio.create_task(self._pipe(process.stdout, writer))
            done, pending = await asyncio.wait(
                [to_lsp, from_lsp], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
        finally:
            writer.close()
            await writer.wait_closed()
            await self._terminate_process(process)

    async def _pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()

    async def _stderr_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            logging.getLogger("lsp").warning("stderr: %s", line.decode().rstrip())

    async def _start_process(self) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stderr:
            asyncio.create_task(self._stderr_loop(process.stderr))
        return process

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        async with self.process_lock:
            if self.process is None or self.process.returncode is not None:
                self.process = await self._start_process()
            return self.process

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin:
            process.stdin.close()
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose an LSP stdio server over TCP")
    parser.add_argument("--command", required=True, help="LSP server command")
    parser.add_argument("--arg", action="append", default=[], help="Command args")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=2087, help="Listen port")
    parser.add_argument(
        "--mode",
        choices=["shared", "per-client"],
        default="per-client",
        help="shared uses one LSP process; per-client spawns one per connection",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    proxy = LspTcpProxy(args.command, args.arg, args.host, args.port, args.mode)
    asyncio.run(proxy.run())


if __name__ == "__main__":
    main()
