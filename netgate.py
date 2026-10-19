"""STR-7: the one network gate. Every outbound call Dolmi makes (cloud captions, cloud translator,
Assistant/Summarize, model lists, model downloads, update check) asks `check(purpose)` first.

With local-only mode on, `check` raises LocalOnly (a friendly message, never a crash), and as a second
line of defence the socket layer refuses any connection that is not to this computer, so a library that
opens its own connection (an SDK, huggingface_hub) is stopped too."""
import ipaddress
import socket
import threading

MESSAGE = "Local-only mode is on: {what} needs the internet. Turn it off in Settings > Privacy & data."
LABELS = {"captions": "cloud captions", "translate": "the cloud translator", "assistant": "the Assistant",
          "models": "the model list", "download": "downloading a model", "update": "the update check"}


class LocalOnly(RuntimeError):
    """An outbound call refused because local-only mode is on."""

    def __init__(self, purpose="network"):
        self.purpose = purpose
        super().__init__(MESSAGE.format(what=LABELS.get(purpose, "this")))


_lock = threading.Lock()
_on = False
_blocked = []            # (purpose, host) of refused calls, newest last (for tests and the log)
_real = {}


def is_local_only():
    return _on


def blocked():
    return list(_blocked)


def check(purpose, host=""):
    """Raise LocalOnly when local-only mode is on. Call before any outbound request."""
    if _on:
        with _lock:
            _blocked.append((purpose, host))
            del _blocked[:-100]
        raise LocalOnly(purpose)


def _is_loopback(address):
    host = address[0] if isinstance(address, tuple) else address
    if not isinstance(host, str):
        return True                  # AF_UNIX and friends: never the internet
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False                 # a name: resolving it already means going out


def _guard(method):
    def connect(sock, address, *args):
        if _on and sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
            check("network", str(address[0]))
        return method(sock, address, *args)
    return connect


def set_local_only(on):
    """Switch local-only mode. While on, socket connects to anything but loopback raise LocalOnly."""
    global _on
    with _lock:
        if not _real:
            _real["connect"], _real["connect_ex"] = socket.socket.connect, socket.socket.connect_ex
            socket.socket.connect = _guard(_real["connect"])
            socket.socket.connect_ex = _guard(_real["connect_ex"])
        _on = bool(on)
