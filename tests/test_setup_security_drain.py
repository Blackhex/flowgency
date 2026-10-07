import asyncio
import time

from fastapi import Request

from flowgency.web.setup_security import discard_unread_body


def _request(receive) -> Request:
    scope = {"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": []}
    return Request(scope, receive)


def test_discard_stops_after_the_byte_cap():
    received = 0

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    asyncio.run(discard_unread_body(_request(receive), max_bytes=4096))

    assert received == 5


def test_discard_stops_at_the_end_of_the_body():
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    asyncio.run(discard_unread_body(_request(receive), max_bytes=4096))

    assert next(messages, None) is None


def test_discard_never_raises_from_the_transport():
    async def receive():
        raise OSError("connection lost")

    asyncio.run(discard_unread_body(_request(receive), max_bytes=4096))


def test_discard_gives_up_on_a_stalled_client():
    async def receive():
        await asyncio.sleep(30)

    started = time.monotonic()
    asyncio.run(discard_unread_body(_request(receive), max_bytes=4096, timeout=0.2))

    assert time.monotonic() - started < 2


def test_discard_is_skipped_once_the_body_was_fully_read():
    received = 0

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    request = _request(receive)
    request.state.body_fully_read = True

    asyncio.run(discard_unread_body(request, max_bytes=4096))

    assert received == 0
