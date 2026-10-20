"""Windows Core Audio through plain ctypes (no comtypes/pycaw dependency): render endpoints and their
volume/mute, per-process audio sessions (ISimpleAudioVolume), per-app output routing
(IAudioPolicyConfig, undocumented, the same interface EarTrumpet and SoundSwitch use) and WASAPI
process loopback capture (ActivateAudioInterfaceAsync with PROCESS_LOOPBACK, Windows 10 2004+).

Used by voice/routing_spike.py (VOICE-4) and voice/routing_win.py (VOICE-5). Every Windows call is made
inside functions, so the module imports on any OS. COM objects are released when the Python wrapper is
garbage collected. Functions that touch COM call com_init() for the calling thread first.
"""
import ctypes
import os
import sys
import threading
import uuid
from ctypes import POINTER, Structure, byref, c_float, c_int, c_long, c_uint, c_ulong, c_ushort, c_void_p

if sys.platform == "win32":
    from ctypes import WINFUNCTYPE
else:                                   # importable (and unit-testable) elsewhere
    WINFUNCTYPE = ctypes.CFUNCTYPE

HRESULT = c_long
E_NOINTERFACE = -2147467262             # 0x80004002
CLSCTX_ALL = 23
E_RENDER, E_CAPTURE = 0, 1
ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS = 0, 1, 2
DEVICE_STATE_ACTIVE = 1
VT_LPWSTR, VT_BLOB = 31, 65

# Device interface path pieces for IAudioPolicyConfig (as EarTrumpet builds them).
MMDEVAPI_TOKEN = "\\\\?\\SWD#MMDEVAPI#"
RENDER_TOKEN = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"


class GUID(Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IUnknown = "00000000-0000-0000-C000-000000000046"
IID_IAgileObject = "94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
IID_IAudioSessionManager2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"
IID_IAudioSessionControl2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"
IID_ISimpleAudioVolume = "87CE5498-68D6-44E5-9215-6DA47EF883D8"
IID_IAudioClient = "1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"
IID_IAudioCaptureClient = "C8ADBD64-E71E-48A0-A4DE-185C395CD317"
IID_IActivateCompletion = "41D949AB-9862-444A-80F6-C261334DA5EB"
# IAudioPolicyConfigFactory: Windows 10 21H2 / Windows 11 and later, then the older 1803-21H1 one.
IID_POLICY_FACTORIES = ("AB3D4648-E242-459F-B02F-541C70306324", "2A59116D-6C4F-45E0-A74F-707E3FEF9258")
POLICY_CLASS = "Windows.Media.Internal.AudioPolicyConfig"
POLICY_SET, POLICY_GET = 25, 26         # vtable slots after IInspectable (6) + 19 methods
PKEY_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)

_tls = threading.local()


def com_init():
    """CoInitializeEx(MTA) once per thread (process loopback activation needs the MTA)."""
    if getattr(_tls, "init", False):
        return
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
    if hr not in (0, 1, -2147417850):   # S_OK, S_FALSE, RPC_E_CHANGED_MODE (already STA: still usable)
        raise OSError(f"CoInitializeEx failed: {hr:#x}")
    _tls.init = True


class Com:
    """A COM interface pointer; .call(slot, argtypes, *args) calls vtable slot `slot`."""

    def __init__(self, ptr):
        self.ptr = ptr.value if isinstance(ptr, c_void_p) else ptr
        if not self.ptr:
            raise OSError("null COM pointer")

    def call(self, slot, argtypes, *args, restype=HRESULT):
        vtbl = ctypes.cast(c_void_p(self.ptr), POINTER(POINTER(c_void_p)))[0]
        fn = WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[slot])
        return fn(self.ptr, *args)

    def out(self, slot, argtypes, *args):
        """Call a method whose last parameter is an interface out-pointer; returns that interface."""
        p = c_void_p()
        self.call(slot, list(argtypes) + [POINTER(c_void_p)], *args, byref(p))
        return Com(p)

    def query(self, iid):
        return self.out(0, [POINTER(GUID)], byref(GUID.of(iid)))

    def __del__(self):
        try:
            if self.ptr:
                self.call(2, [], restype=c_ulong)
                self.ptr = None
        except Exception:               # interpreter shutdown: nothing useful to do
            pass


def _co_create(clsid, iid):
    com_init()
    p = c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(byref(GUID.of(clsid)), None, CLSCTX_ALL,
                                             byref(GUID.of(iid)), byref(p))
    if hr != 0:
        raise OSError(f"CoCreateInstance failed: {hr & 0xFFFFFFFF:#x}")
    return Com(p)


def _enumerator():
    return _co_create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)


def _device_id(dev):
    s = ctypes.c_wchar_p()
    dev.call(5, [POINTER(ctypes.c_wchar_p)], byref(s))
    try:
        return s.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(s)


def _friendly_name(dev):
    store = dev.out(4, [c_ulong], 0)    # STGM_READ
    key = (ctypes.c_ubyte * 20)()
    ctypes.memmove(key, uuid.UUID(PKEY_FRIENDLY_NAME[0]).bytes_le, 16)
    ctypes.c_ulong.from_buffer(key, 16).value = PKEY_FRIENDLY_NAME[1]
    pv = (ctypes.c_ubyte * 24)()
    store.call(5, [c_void_p, c_void_p], ctypes.addressof(key), ctypes.addressof(pv))
    try:
        if c_ushort.from_buffer(pv).value == VT_LPWSTR:
            return ctypes.c_wchar_p(c_void_p.from_buffer(pv, 8).value).value or ""
        return ""
    finally:
        ctypes.windll.ole32.PropVariantClear(byref(pv))


def render_devices():
    """Active speakers/headphones: [{"id", "name", "default"}]."""
    en = _enumerator()
    default = _device_id(en.out(4, [c_int, c_int], E_RENDER, ROLE_MULTIMEDIA))
    coll = en.out(3, [c_int, c_ulong], E_RENDER, DEVICE_STATE_ACTIVE)
    n = c_uint()
    coll.call(3, [POINTER(c_uint)], byref(n))
    out = []
    for i in range(n.value):
        dev = coll.out(4, [c_uint], i)
        did = _device_id(dev)
        out.append({"id": did, "name": _friendly_name(dev), "default": did == default})
    return out


def _device(device_id=None):
    en = _enumerator()
    if device_id:
        return en.out(5, [ctypes.c_wchar_p], device_id)
    return en.out(4, [c_int, c_int], E_RENDER, ROLE_MULTIMEDIA)


def _activate(dev, iid):
    return dev.out(3, [POINTER(GUID), c_ulong, c_void_p], byref(GUID.of(iid)), CLSCTX_ALL, None)


class EndpointVolume:
    """Master volume (0..1) and mute of one render endpoint (default speaker if device_id is None)."""

    def __init__(self, device_id=None):
        self._v = _activate(_device(device_id), IID_IAudioEndpointVolume)

    @property
    def level(self):
        f = c_float()
        self._v.call(9, [POINTER(c_float)], byref(f))
        return f.value

    @level.setter
    def level(self, value):
        self._v.call(7, [c_float, c_void_p], float(value), None)

    @property
    def muted(self):
        b = c_int()
        self._v.call(15, [POINTER(c_int)], byref(b))
        return bool(b.value)

    @muted.setter
    def muted(self, value):
        self._v.call(14, [c_int, c_void_p], int(bool(value)), None)


def sessions(device_id=None):
    """Audio sessions on one endpoint: [(pid, ISimpleAudioVolume Com, state)] (state 1 = active)."""
    mgr = _activate(_device(device_id), IID_IAudioSessionManager2)
    en = mgr.out(5, [])
    n = c_int()
    en.call(3, [POINTER(c_int)], byref(n))
    out = []
    for i in range(n.value):
        ctl = en.out(4, [c_int], i)
        c2 = ctl.query(IID_IAudioSessionControl2)
        pid, state = c_ulong(), c_int()
        c2.call(14, [POINTER(c_ulong)], byref(pid))
        c2.call(3, [POINTER(c_int)], byref(state))
        out.append((pid.value, ctl.query(IID_ISimpleAudioVolume), state.value))
    return out


def session_mute(simple, value=None):
    """Read (value None) or set the mute of one ISimpleAudioVolume; returns the mute state before."""
    b = c_int()
    simple.call(6, [POINTER(c_int)], byref(b))
    if value is not None:
        simple.call(5, [c_int, c_void_p], int(bool(value)), None)
    return bool(b.value)


# ------------------------------------------------------------------ per-app routing (IAudioPolicyConfig)
def _hstring(text):
    h = c_void_p()
    if text:
        hr = ctypes.windll.combase.WindowsCreateString(ctypes.c_wchar_p(text), len(text), byref(h))
        if hr != 0:
            raise OSError(f"WindowsCreateString failed: {hr:#x}")
    return h


class PolicyConfig:
    """Per-app default output device: what Settings > Sound > Volume mixer > App volume sets."""

    def __init__(self):
        com_init()
        name = _hstring(POLICY_CLASS)
        try:
            for iid in IID_POLICY_FACTORIES:
                p = c_void_p()
                hr = ctypes.windll.combase.RoGetActivationFactory(name, byref(GUID.of(iid)), byref(p))
                if hr == 0:
                    self._f = Com(p)
                    return
            raise OSError("IAudioPolicyConfig is not available on this Windows version")
        finally:
            ctypes.windll.combase.WindowsDeleteString(name)

    @staticmethod
    def interface_id(device_id):
        return f"{MMDEVAPI_TOKEN}{device_id}{RENDER_TOKEN}" if device_id else ""

    @staticmethod
    def device_id(interface_id):
        """'\\\\?\\SWD#MMDEVAPI#{0.0.0.00000000}.{guid}#{...}' -> '{0.0.0.00000000}.{guid}'."""
        s = interface_id or ""
        if s.startswith(MMDEVAPI_TOKEN):
            s = s[len(MMDEVAPI_TOKEN):]
        if s.endswith(RENDER_TOKEN):
            s = s[:-len(RENDER_TOKEN)]
        return s

    def get(self, pid):
        """The device id this app is routed to, or '' when it follows the Windows default."""
        h = c_void_p()
        self._f.call(POLICY_GET, [c_uint, c_int, c_int, POINTER(c_void_p)], pid, E_RENDER,
                     ROLE_MULTIMEDIA, byref(h))
        if not h.value:
            return ""
        try:
            n = c_uint()
            raw = ctypes.windll.combase.WindowsGetStringRawBuffer
            raw.restype = ctypes.c_wchar_p
            return self.device_id(raw(h, byref(n)) or "")
        finally:
            ctypes.windll.combase.WindowsDeleteString(h)

    def set(self, pid, device_id):
        """Route app `pid` to device_id ('' = back to the Windows default), console + multimedia roles."""
        h = _hstring(self.interface_id(device_id))
        try:
            for role in (ROLE_MULTIMEDIA, ROLE_CONSOLE):
                self._f.call(POLICY_SET, [c_uint, c_int, c_int, c_void_p], pid, E_RENDER, role, h)
        finally:
            if h.value:
                ctypes.windll.combase.WindowsDeleteString(h)


# ------------------------------------------------------------------ process loopback capture
class _ActivationParams(Structure):
    _fields_ = [("type", c_int), ("pid", c_ulong), ("mode", c_int)]


class _Blob(Structure):              # PROPVARIANT holding a VT_BLOB (x64 and x86 layouts)
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("cb", c_ulong), ("data", c_void_p), ("pad", c_void_p)]


class _WaveFormat(Structure):
    _pack_ = 1
    _fields_ = [("tag", c_ushort), ("channels", c_ushort), ("rate", c_ulong), ("bytes_per_sec", c_ulong),
                ("block", c_ushort), ("bits", c_ushort), ("cb", c_ushort)]


_QI = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
_REF = WINFUNCTYPE(c_ulong, c_void_p)
_DONE = WINFUNCTYPE(HRESULT, c_void_p, c_void_p)


class _HandlerVtbl(Structure):
    _fields_ = [("qi", _QI), ("addref", _REF), ("release", _REF), ("done", _DONE)]


class _Handler(Structure):
    _fields_ = [("vtbl", POINTER(_HandlerVtbl))]


class _CompletionHandler:
    """A static IActivateAudioInterfaceCompletionHandler (+IAgileObject) that sets an Event."""

    _ok = {bytes(GUID.of(i)) for i in (IID_IUnknown, IID_IAgileObject, IID_IActivateCompletion)}

    def __init__(self):
        self.done = threading.Event()

        def qi(this, riid, ppv):
            if bytes(riid.contents) in self._ok:
                ppv[0] = this
                return 0
            ppv[0] = None
            return E_NOINTERFACE

        def done(this, op):
            self.done.set()
            return 0

        self._fns = (_QI(qi), _REF(lambda this: 1), _REF(lambda this: 1), _DONE(done))
        self._vtbl = _HandlerVtbl(*self._fns)
        self._obj = _Handler(ctypes.pointer(self._vtbl))
        self.ptr = ctypes.addressof(self._obj)


class ProcessLoopback:
    """Capture what process `pid` (and, include_tree, its child processes) plays, whatever endpoint it
    plays on. read(timeout) returns raw 16-bit PCM bytes (rate/channels as asked) or b'' (no packet)."""

    FLAGS = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000   # LOOPBACK|EVENTCALLBACK|AUTOCONVERTPCM|SRC

    def __init__(self, pid, include_tree=True, rate=16000, channels=1):
        com_init()
        self.rate, self.channels = rate, channels
        params = _ActivationParams(1, pid, 0 if include_tree else 1)
        blob = _Blob(vt=VT_BLOB, cb=ctypes.sizeof(params), data=ctypes.addressof(params))
        handler = _CompletionHandler()
        op = c_void_p()
        fn = ctypes.windll.mmdevapi.ActivateAudioInterfaceAsync
        fn.argtypes = [ctypes.c_wchar_p, POINTER(GUID), c_void_p, c_void_p, POINTER(c_void_p)]
        fn.restype = HRESULT
        fn("VAD\\Process_Loopback", byref(GUID.of(IID_IAudioClient)), ctypes.addressof(blob),
           handler.ptr, byref(op))
        op = Com(op)
        if not handler.done.wait(5):
            raise OSError("process loopback activation timed out")
        hr, unk = HRESULT(), c_void_p()
        op.call(3, [POINTER(HRESULT), POINTER(c_void_p)], byref(hr), byref(unk))
        if hr.value != 0:
            raise OSError(f"process loopback activation failed: {hr.value & 0xFFFFFFFF:#x}")
        self._client = Com(unk)
        block = channels * 2
        wfx = _WaveFormat(1, channels, rate, rate * block, block, 16, 0)
        self._client.call(3, [c_int, c_ulong, ctypes.c_longlong, ctypes.c_longlong, c_void_p, c_void_p],
                          0, self.FLAGS, 2_000_000, 0, ctypes.addressof(wfx), None)
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = c_void_p
        self._event = k32.CreateEventW(None, 0, 0, None)
        self._client.call(13, [c_void_p], self._event)
        self._cap = self._client.out(14, [POINTER(GUID)], byref(GUID.of(IID_IAudioCaptureClient)))
        self._block = block
        self._client.call(10, [])

    def read(self, timeout=0.1):
        ctypes.windll.kernel32.WaitForSingleObject(c_void_p(self._event), int(timeout * 1000))
        chunks = []
        while True:
            n = c_uint()
            self._cap.call(5, [POINTER(c_uint)], byref(n))
            if not n.value:
                break
            data, frames, flags = c_void_p(), c_uint(), c_ulong()
            self._cap.call(3, [POINTER(c_void_p), POINTER(c_uint), POINTER(c_ulong), c_void_p, c_void_p],
                           byref(data), byref(frames), byref(flags), None, None)
            size = frames.value * self._block
            chunks.append(b"\0" * size if flags.value & 0x2 else ctypes.string_at(data, size))
            self._cap.call(4, [c_uint], frames.value)
        return b"".join(chunks)

    def close(self):
        if getattr(self, "_client", None):
            try:
                self._client.call(11, [])
            finally:
                ctypes.windll.kernel32.CloseHandle(c_void_p(self._event))
                self._client = self._cap = None


# ------------------------------------------------------------------ processes
class _ProcessEntry(Structure):
    _fields_ = [("size", c_ulong), ("usage", c_ulong), ("pid", c_ulong), ("heap", ctypes.c_size_t),
                ("module", c_ulong), ("threads", c_ulong), ("ppid", c_ulong), ("prio", c_long),
                ("flags", c_ulong), ("exe", ctypes.c_wchar * 260)]


def processes():
    """[(pid, parent pid, exe name lower-case)] of every running process (Toolhelp snapshot)."""
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = c_void_p
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == c_void_p(-1).value:
        raise OSError("process snapshot failed")
    try:
        e = _ProcessEntry()
        e.size = ctypes.sizeof(e)
        out = []
        ok = k32.Process32FirstW(c_void_p(snap), byref(e))
        while ok:
            out.append((e.pid, e.ppid, e.exe.lower()))
            ok = k32.Process32NextW(c_void_p(snap), byref(e))
        return out
    finally:
        k32.CloseHandle(c_void_p(snap))


def available():
    return sys.platform == "win32" and int(sys.getwindowsversion().build) >= 19041
