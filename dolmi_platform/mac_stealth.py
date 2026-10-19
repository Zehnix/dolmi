"""Screen-share privacy for Dolmi's windows (macOS).

"Invisible mode" keeps a window fully visible to the person sitting at the Mac, but leaves it
out of screen shares and recordings, and takes Dolmi off the Dock and the ⌘-Tab switcher. It uses
NSWindow.sharingType = NSWindowSharingNone — the documented AppKit setting password managers use to
keep their windows out of screenshots — and the "accessory" activation policy for the Dock.

It does NOT hide anything from Activity Monitor. Screen sharing shows the shared screen, not
Activity Monitor, so there is no need to.

On macOS 27 the window server makes NSWindowSharingNone one-way: a window that has been hidden from
capture can't be made capturable again (setSharingType_ is ignored). capture_allowed() tells whether
it took; the caller replaces the window with a fresh one when it didn't.

Every AppKit call here runs on the main thread (AppHelper.callAfter); callers can be on any thread.
"""
import ctypes
import threading

NS_SHARING_NONE, NS_SHARING_READONLY = 0, 1
NS_POLICY_REGULAR, NS_POLICY_ACCESSORY = 0, 1
# NSWindowCollectionBehavior: on every Space, and above other apps' full-screen windows
NS_ALL_SPACES, NS_STATIONARY, NS_FULLSCREEN_AUX = 1 << 0, 1 << 4, 1 << 8


def _main(fn, *args, wait=False):
    """Run fn on the AppKit main thread (immediately if this already is it); wait=True blocks until done."""
    from Foundation import NSThread
    from PyObjCTools import AppHelper
    if NSThread.isMainThread():
        fn(*args)
        return
    done = threading.Event()

    def run():
        try:
            fn(*args)
        finally:
            done.set()
    AppHelper.callAfter(run)
    if wait:
        done.wait(5)


def exclude_from_capture(nswindow, on):
    """Hide the window from screen capture (True) or ask to show it again (False).
    Returns whether the window ended up as asked (see capture_allowed for the one-way case)."""
    if nswindow is None:
        return False
    _main(nswindow.setSharingType_, NS_SHARING_NONE if on else NS_SHARING_READONLY, wait=True)
    return capture_allowed(nswindow) != on


def capture_allowed(nswindow):
    """True when screen shares and recordings can see this window."""
    return nswindow is not None and nswindow.sharingType() != NS_SHARING_NONE


def hide_from_dock(on, icon=None):
    """Take Dolmi off the Dock and ⌘-Tab (True) or put it back (False). Windows stay where they are."""
    def run():
        import AppKit
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(NS_POLICY_ACCESSORY if on else NS_POLICY_REGULAR)
        if not on and icon:   # the Dock shows the default Python icon again unless it's re-set
            image = AppKit.NSImage.alloc().initByReferencingFile_(str(icon))
            if image:
                app.setApplicationIconImage_(image)
        app.activateIgnoringOtherApps_(True)   # switching policy can drop focus
    _main(run)


def float_everywhere(nswindow):
    """The subtitle bar: on every Space and above full-screen meetings, never in ⌘-` window cycling."""
    if nswindow is None:
        return
    def run():
        import AppKit
        nswindow.setCollectionBehavior_(NS_ALL_SPACES | NS_STATIONARY | NS_FULLSCREEN_AUX)
        nswindow.setHidesOnDeactivate_(False)
        nswindow.setLevel_(AppKit.NSStatusWindowLevel)
    _main(run)


def apply(nswindow, invisible, dock=True, icon=None):
    """Apply (or clear) invisible mode on one window. `dock` False leaves the Dock icon alone."""
    if dock:
        try:
            hide_from_dock(invisible, icon)
        except Exception as e:   # the capture exclusion is what matters; the Dock is a nicety
            print(f"Invisible mode: could not change the Dock icon ({e})")
    return exclude_from_capture(nswindow, invisible)


# ------------------------------------------------------------------ global hotkey (always-works escape hatch)
# Carbon's RegisterEventHotKey: system-wide, and unlike an NSEvent monitor it needs no Accessibility
# permission. Modifier masks and the virtual key code for D are from Carbon/HIToolbox.
MOD_CMD, MOD_SHIFT, MOD_ALT, MOD_CONTROL = 0x0100, 0x0200, 0x0800, 0x1000
VK_D = 0x02

_carbon = None
_hotkeys = []   # registered hotkeys stay referenced: Carbon calls their ctypes callbacks for the app's lifetime


def _fourcc(s):
    return int.from_bytes(s.encode("ascii"), "big")


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class GlobalHotkey:
    """A system-wide hotkey, so Dolmi can always be turned visible again even when it is off the Dock
    and ⌘-Tab and buried behind other windows. `on_press` runs on its own thread (the main thread
    must stay free: pywebview's evaluate_js waits for it)."""

    def __init__(self, mods, vk, on_press, name="hotkey"):
        self.mods, self.vk, self.on_press, self.hotkey_name = mods, vk, on_press, name
        self.registered = threading.Event()
        self.ok = False
        self._handler = self._ref = None   # kept alive for as long as the hotkey exists

    def start(self):
        _hotkeys.append(self)
        _main(self._register)
        return self

    def _register(self):
        global _carbon
        try:
            if _carbon is None:
                _carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
                _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
                _carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_uint32,
                                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                _carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID,
                                                        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            target = _carbon.GetApplicationEventTarget()
            spec = _EventTypeSpec(_fourcc("keyb"), 5)          # kEventClassKeyboard, kEventHotKeyPressed
            self._handler = _HANDLER(self._fired)
            st = _carbon.InstallEventHandler(target, self._handler, 1, ctypes.byref(spec), None, None)
            ref = ctypes.c_void_p()
            if st == 0:
                st = _carbon.RegisterEventHotKey(self.vk, self.mods, _HotKeyID(_fourcc("DLMI"), 1),
                                                 target, 0, ctypes.byref(ref))
            self._ref = ref
            self.ok = st == 0
            if not self.ok:
                print(f"Dolmi: could not register the {self.hotkey_name} global hotkey (status {st}; another app may use it)")
        except Exception as e:
            print(f"Dolmi: could not register the {self.hotkey_name} global hotkey ({e})")
        self.registered.set()

    def _fired(self, _call, _event, _data):
        def run():
            try:
                self.on_press()
            except Exception as e:
                print(f"Dolmi hotkey handler error: {e}")
        threading.Thread(target=run, daemon=True).start()
        return 0
