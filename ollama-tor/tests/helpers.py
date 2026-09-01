from __future__ import annotations

import socket
import socketserver
import struct
import threading


def read_exact(stream: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise EOFError("client disconnected")
        result.extend(chunk)
    return bytes(result)


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RunningServer:
    def __init__(self, handler: type[socketserver.BaseRequestHandler]):
        self.server = ThreadedServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class FakeSocks:
    def __init__(self, *, reply: int = 0):
        self.reply = reply
        self.requests: list[dict[str, object]] = []
        self.event = threading.Event()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                stream = self.request
                version, count = read_exact(stream, 2)
                assert version == 5
                methods = read_exact(stream, count)
                selected = 2 if 2 in methods else 0
                stream.sendall(bytes([5, selected]))
                username = password = None
                if selected == 2:
                    assert read_exact(stream, 1) == b"\x01"
                    username = read_exact(stream, read_exact(stream, 1)[0]).decode()
                    password = read_exact(stream, read_exact(stream, 1)[0]).decode()
                    stream.sendall(b"\x01\x00")

                version, command, reserved, address_type = read_exact(stream, 4)
                assert (version, command, reserved) == (5, 1, 0)
                if address_type == 1:
                    host = socket.inet_ntoa(read_exact(stream, 4))
                elif address_type == 4:
                    host = socket.inet_ntop(socket.AF_INET6, read_exact(stream, 16))
                else:
                    assert address_type == 3
                    host = read_exact(stream, read_exact(stream, 1)[0]).decode("idna")
                port = struct.unpack("!H", read_exact(stream, 2))[0]
                owner.requests.append(
                    {
                        "host": host,
                        "port": port,
                        "username": username,
                        "password": password,
                    }
                )
                stream.sendall(
                    bytes([5, owner.reply, 0, 1]) + b"\x00\x00\x00\x00\x00\x00"
                )
                owner.event.set()

        self.running = RunningServer(Handler)

    @property
    def port(self) -> int:
        return self.running.port

    def __enter__(self):
        self.running.__enter__()
        return self

    def __exit__(self, *exc):
        self.running.__exit__(*exc)


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            self.request.sendall(chunk)
