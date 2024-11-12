import asyncio
import platform
import time
import socket
from contextlib import contextmanager
from functools import partial
from threading import Thread

import zmq
import pytest
import trio
from tornado.platform.asyncio import SelectorThread

URL = "tcp://127.0.0.1:5555"


def spawn_sender_thread(url, delay=2):
    with zmq.Context() as ctx, ctx.socket(zmq.PUSH) as s:
        s.linger = 3_000
        s.bind(url)
        time.sleep(delay)
        try:
            s.send(b"message", zmq.NOBLOCK)
        except zmq.Again:
            print("no receiver...")


async def asyncio_wait_readable(fd, timeout=10):
    loop = asyncio.get_event_loop()
    if platform.system() == "Windows":
        selector = SelectorThread(loop)
    else:
        selector = loop

    f = asyncio.Future()
    def callback(*args):
        selector.remove_reader(fd)
        # done with selector
        if platform.system() == "Windows":
            selector.close()
        if not f.done():
            f.set_result(None)

    def cancel():
        if not f.done():
            f.set_exception(TimeoutError())

    loop.call_later(5, cancel)
    selector.add_reader(fd, callback)
    return await f


@contextmanager
def get_fd(zmq_sock):
    yield zmq_sock.fileno()


@contextmanager
def socket_fromfd(zmq_sock):
    with socket.fromfd(zmq_sock.fileno(), socket.AF_INET, socket.SOCK_STREAM) as sock:
        yield sock


@contextmanager
def socket_fileno(zmq_sock):
    sock = socket.socket(fileno=zmq_sock.fileno())
    try:
        yield sock
    finally:
        sock.detach()


async def receiver(url, waiter, get_handle):
    with zmq.Context() as ctx, ctx.socket(zmq.PULL) as s:
        s.linger = 0
        s.connect(URL)
        tic = time.monotonic()
        with get_handle(s) as handle:
            while not s.EVENTS & zmq.POLLIN:
                await waiter(handle)
        toc = time.monotonic()
        s.recv(zmq.NOBLOCK)
        return toc - tic


def test_socket_fromfd():
    with zmq.Context() as ctx, ctx.socket(zmq.PULL) as s:
        signal_socket = socket.fromfd(s.fileno(), socket.AF_INET, socket.SOCK_STREAM)
        signal_socket.close()


def test_socket_fileno():
    with zmq.Context() as ctx, ctx.socket(zmq.PULL) as s:
        signal_socket = socket.socket(fileno=s.fileno())
        signal_socket.detach()


@pytest.mark.parametrize("runner", ["asyncio", "trio"])
@pytest.mark.parametrize("get_handle", [get_fd, socket_fromfd, socket_fileno])
def test_async_wait(runner, get_handle):
    if runner == "asyncio":
        run = asyncio.run
        f = receiver(URL, asyncio_wait_readable, get_handle)
    elif runner == "trio":
        run = trio.run
        f = partial(receiver, URL, trio.lowlevel.wait_readable, get_handle)
    sender_thread = Thread(target=spawn_sender_thread, args=(URL,))
    sender_thread.start()
    try:
        wait_time = run(f)
    finally:
        sender_thread.join()
    # make sure it waited an appropriate amount of time
    assert 1 < wait_time < 3
