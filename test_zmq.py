import asyncio
import time
from threading import Thread

import zmq
import trio
from tornado.platform.asyncio import SelectorThread

URL = "tcp://127.0.0.1:5555"


def spawn_sender_thread(url, delay=2):
    with zmq.Context() as ctx, ctx.socket(zmq.PUSH) as s:
        s.linger = 3_000
        s.bind(url)
        time.sleep(delay)
        s.send(b"message")


async def asyncio_wait_readable(fd, timeout=10):
    loop = asyncio.get_event_loop()
    selector = SelectorThread(loop)
    f = asyncio.Future()

    def callback(*args):
        # done with selector
        selector.remove_reader(fd)
        selector.close()
        f.set_result(None)

    def cancel():
        if not f.done():
            f.cancel()

    loop.call_later(10, cancel)
    selector.add_reader(fd, callback)
    return await f


async def receiver(url, waiter):
    with zmq.Context() as ctx, ctx.socket(zmq.PULL) as s:
        s.linger = 0
        s.connect(URL)
        tic = time.monotonic()
        while not s.EVENTS & zmq.POLLIN:
            await waiter(s.fileno())
        toc = time.monotonic()
        s.recv(zmq.NOBLOCK)
        return toc - tic


def test_asyncio():
    sender_thread = Thread(target=spawn_sender_thread, args=(URL,))
    sender_thread.start()
    wait_time = asyncio.run(receiver(URL, asyncio_wait_readable))
    sender_thread.join()
    assert 1 < wait_time < 3


def test_trio():
    sender_thread = Thread(target=spawn_sender_thread, args=(URL,))
    sender_thread.start()

    wait_time = trio.run(receiver, URL, trio.lowlevel.wait_readable)
    sender_thread.join()
    assert 1 < wait_time < 3
