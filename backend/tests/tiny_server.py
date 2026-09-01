"""A hand-rolled HTTP server for testing the outbound client.

Deliberately raw sockets rather than a framework. The interesting cases here are
the badly behaved ones -- a redirect, an endless response body, a connection that
accepts and then says nothing -- and a framework makes those awkward to express
while a socket makes them three lines.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Handler = Callable[[bytes, asyncio.StreamWriter], Awaitable[None]]


@dataclass
class Request:
    """The bits of the incoming request the tests care about."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


def parse(raw: bytes) -> Request:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return Request(method, path, headers, body)


def respond(status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None) -> bytes:
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}"]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


@dataclass
class TinyServer:
    """Serves one handler on a random loopback port."""

    handler: Handler
    received: list[Request] = field(default_factory=list)
    port: int = 0
    _server: asyncio.Server | None = None
    _handlers: set[asyncio.Task[None]] = field(default_factory=set)

    async def __aenter__(self) -> "TinyServer":
        async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task is not None:
                self._handlers.add(task)
            try:
                raw = await asyncio.wait_for(reader.read(65536), timeout=5)
                if raw:
                    self.received.append(parse(raw))
                    await self.handler(raw, writer)
            except (TimeoutError, ConnectionError, asyncio.CancelledError):
                pass
            finally:
                if task is not None:
                    self._handlers.discard(task)
                writer.close()

        self._server = await asyncio.start_server(on_client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        # Handlers are cancelled rather than awaited. `wait_closed` waits for open
        # connections to finish, and a handler that deliberately stalls would hold
        # the whole suite hostage for as long as it decided to sleep.
        for task in list(self._handlers):
            task.cancel()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


def echoes(token_header: str) -> Handler:
    """Answer with exactly the challenge token, the way a real destination would."""

    async def handler(raw: bytes, writer: asyncio.StreamWriter) -> None:
        token = parse(raw).headers.get(token_header, "")
        writer.write(respond(200, token.encode()))
        await writer.drain()

    return handler


def answers(status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None) -> Handler:
    async def handler(raw: bytes, writer: asyncio.StreamWriter) -> None:
        writer.write(respond(status, body, headers))
        await writer.drain()

    return handler


def streams_forever() -> Handler:
    """Never stops sending. A destination can be hostile as easily as broken."""

    async def handler(raw: bytes, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 X\r\n\r\n")
        try:
            while True:
                writer.write(b"x" * 8192)
                await writer.drain()
        except (ConnectionError, RuntimeError):
            pass

    return handler


def stalls() -> Handler:
    """Accepts the connection and then says nothing at all.

    Bounded anyway: the client under test is given a far shorter timeout, so this
    only has to outlast it, and a stray sleep must never be able to slow the suite
    down on its own.
    """

    async def handler(raw: bytes, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    return handler
