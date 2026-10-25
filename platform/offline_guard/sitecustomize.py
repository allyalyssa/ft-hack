"""Refuse every network connection that leaves this machine, in any Python process started with this on PYTHONPATH.

PYTHONPATH=platform/offline_guard python3 platform/run.py

Blocked attempts raise OSError, are printed to stderr and, if OFFLINE_GUARD_LOG is set, appended to that file.
"""
import ipaddress
import os
import socket
import sys


def _local(host):
    if isinstance(host, bytes):
        host = host.decode()
    if host in (None, "", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _blocked(host):
    message = f"offline guard: blocked network access to {host}"
    print(message, file=sys.stderr)
    if os.environ.get("OFFLINE_GUARD_LOG"):
        with open(os.environ["OFFLINE_GUARD_LOG"], "a", encoding="utf-8") as f:
            f.write(f"{host}\n")
    return OSError(message)


_getaddrinfo = socket.getaddrinfo
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def getaddrinfo(host, *args, **kwargs):
    if not _local(host):
        raise _blocked(host)
    return _getaddrinfo(host, *args, **kwargs)


def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
        raise _blocked(address[0])
    return _connect(self, address)


def connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
        raise _blocked(address[0])
    return _connect_ex(self, address)


socket.getaddrinfo = getaddrinfo
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
