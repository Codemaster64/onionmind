"""Fail closed when a routed Python child tries to bypass the Tor proxy."""

import ipaddress
import socket

_OFF = "ollama-tor: direct network access is off; use the Tor proxy environment"
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_sendto = socket.socket.sendto
_sendmsg = getattr(socket.socket, "sendmsg", None)
_dns_functions = {
    name: getattr(socket, name)
    for name in (
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "getnameinfo",
    )
    if hasattr(socket, name)
}


def _text(host):
    if isinstance(host, bytes):
        try:
            return host.decode("ascii")
        except UnicodeDecodeError:
            return ""
    return str(host)


def _local_host(host):
    if host is None:
        return True
    value = _text(host).strip().strip("[]")
    if not value or value.lower().rstrip(".") == "localhost":
        return True
    if "%" in value:
        value = value.partition("%")[0]
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _address_host(address):
    # A string or bytes address is a Unix-domain socket path, not an IP socket.
    return address[0] if isinstance(address, tuple) and address else None


def _guard(address):
    if not _local_host(_address_host(address)):
        raise OSError(_OFF)


def connect(self, address):
    _guard(address)
    return _connect(self, address)


def connect_ex(self, address):
    _guard(address)
    return _connect_ex(self, address)


def sendto(self, data, *args):
    if args and isinstance(args[-1], tuple):
        _guard(args[-1])
    return _sendto(self, data, *args)


def sendmsg(self, buffers, *args):
    if args and isinstance(args[-1], tuple):
        _guard(args[-1])
    return _sendmsg(self, buffers, *args)


def _dns_guard(host):
    if not _local_host(host):
        raise socket.gaierror(_OFF)


def getaddrinfo(host, *args, **kwargs):
    _dns_guard(host)
    return _dns_functions["getaddrinfo"](host, *args, **kwargs)


def gethostbyname(host):
    _dns_guard(host)
    return _dns_functions["gethostbyname"](host)


def gethostbyname_ex(host):
    _dns_guard(host)
    return _dns_functions["gethostbyname_ex"](host)


def gethostbyaddr(host):
    _dns_guard(host)
    return _dns_functions["gethostbyaddr"](host)


def getnameinfo(sockaddr, flags):
    _guard(sockaddr)
    return _dns_functions["getnameinfo"](sockaddr, flags)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.sendto = sendto
if _sendmsg is not None:
    socket.socket.sendmsg = sendmsg
socket.getaddrinfo = getaddrinfo
socket.gethostbyname = gethostbyname
socket.gethostbyname_ex = gethostbyname_ex
socket.gethostbyaddr = gethostbyaddr
socket.getnameinfo = getnameinfo
