"""A minimal SSE client that speaks ASGI directly.

The rest of the suite drives the app through httpx's `ASGITransport`, but that
transport cannot be used here: it runs the application to completion and
concatenates the whole body before handing back a response. Against a feed that
never ends, it simply hangs forever.

Talking to the ASGI callable ourselves gives the chunks as they are produced,
still in-process and without binding a port, and still exercises the real route:
dependencies, status, headers and all.
"""

import asyncio
import contextlib
from typing import Any

Frame = dict[str, str]

# How long `expect_silence` watches before concluding nothing is coming. Proving
# an absence costs real waiting, so it is kept short: it is paid by every test
# that asserts one.
SILENCE_WINDOW = 0.5


def _parse(buffer: str) -> tuple[list[Frame], str]:
    """Split whole SSE frames off the buffer, returning the unfinished tail.

    Frames are separated by a blank line, so anything after the last one is a
    partial frame and has to wait for more bytes. Lines starting with `:` are
    comments -- our heartbeats -- and produce no frame at all, which is exactly
    the behaviour a browser has.
    """
    frames: list[Frame] = []

    while "\n\n" in buffer:
        raw, _, buffer = buffer.partition("\n\n")
        frame: Frame = {}
        for line in raw.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            frame[field] = value.lstrip()
        if frame:
            frames.append(frame)

    return frames, buffer


class SSEStream:
    """One open SSE connection to the application.

    How long a read may wait is set once, on the connection, rather than passed
    to every call: it is a property of this test client, not of each individual
    frame, and keeping it out of the coroutine signatures avoids the nested,
    non-composing deadlines that per-call timeouts tend to grow into.
    """

    def __init__(
        self,
        app: Any,
        path: str,
        query: str = "",
        headers: dict[str, str] | None = None,
        wait: float = 5.0,
    ):
        self._app = app
        self._path = path
        self._query = query
        self._headers = headers or {}
        self._wait = wait
        self._messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._disconnected = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._buffer = ""
        self._pending: list[Frame] = []
        self.status: int | None = None
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> "SSEStream":
        self._task = asyncio.create_task(self._run())
        start = await self._await_message()
        assert start["type"] == "http.response.start"
        self.status = start["status"]
        self.headers = {k.decode(): v.decode() for k, v in start.get("headers", [])}
        return self

    async def __aexit__(self, *_: object) -> None:
        # Mirrors a browser closing the tab: `receive` answers http.disconnect and
        # Starlette tears the generator down.
        self._disconnected.set()
        if self._task is not None:
            self._task.cancel()
            # Awaiting a cancelled task re-raises, and the app may also throw on
            # its way down. Neither says anything about the test that just ran, so
            # both are suppressed -- deliberately and visibly, rather than with a
            # bare except.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _run(self) -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": self._query.encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in self._headers.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }

        async def receive() -> dict[str, Any]:
            await self._disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            await self._messages.put(message)

        await self._app(scope, receive, send)

    async def _await_message(self) -> dict[str, Any]:
        async with asyncio.timeout(self._wait):
            return await self._messages.get()

    async def next_frame(self) -> Frame:
        """The next event, waiting for it if necessary.

        Every wait is bounded: an unbounded read against a feed that is broken
        would hang the whole suite instead of failing one test.
        """
        while not self._pending:
            message = await self._await_message()
            if message["type"] != "http.response.body":
                continue
            self._buffer += message.get("body", b"").decode()
            frames, self._buffer = _parse(self._buffer)
            self._pending.extend(frames)

        return self._pending.pop(0)

    async def next_frames(self, count: int) -> list[Frame]:
        return [await self.next_frame() for _ in range(count)]

    async def expect_silence(self) -> None:
        """Assert that nothing arrives within `SILENCE_WINDOW`.

        Needed for the isolation test: proving an event does NOT reach a stream
        can only be done by waiting and finding nothing.
        """
        # The try wraps the `async with`, not the await inside it: `asyncio.timeout`
        # cancels the inner await and raises TimeoutError from its __aexit__, so an
        # except placed inside the block would never see it.
        try:
            async with asyncio.timeout(SILENCE_WINDOW):
                message = await self._messages.get()
        except TimeoutError:
            return

        raise AssertionError(f"unexpected message: {message}")
