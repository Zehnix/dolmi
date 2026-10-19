"""Everything Dolmi does differently on Windows and macOS, behind one API.

The rest of the program (live_subs, models, assistant, config, the webview Api, main) is shared and
imports from here. windows.py and mac.py define the same names; this picks one by sys.platform.
Both modules import on any OS (OS libraries load lazily inside functions), so tests can check both.

    capture   output_devices() input_devices() loopback_stream(out_q, stop, device=None, on_level=None)
              mic_stream(out_q, stop, device=None, on_level=None) DEFAULT_SPEAKER
    compute   prepare_gpu() compute_device(device) -> (device, compute_type) auto_speech(gpu=None)
              gpu_ready() system_info() gpu_name()
    desktop   copy_text(text) -> bool  open_path(path)  mic_privacy_url  register_app()  app_icon(root)
              WEBVIEW_GUI
    windows   overlay_stealth(api)  apply_invisible(api, on)  set_click_through(window, on) -> bool
              click_through(window) -> bool   (subtitle bar lock: clicks go to the window below)
    keys      protect(bytes) -> bytes  unprotect(bytes) -> bytes   (DPAPI / Keychain secret)
    folders   config_dir()  log_dir()  documents_folder()  needs_log_file()
    hotkey    start_recover_hotkey(on_press)
    instance  single_instance(name="Dolmi") -> guard (.listen(on_activate), .close()) | None if one runs
              bring_to_front(window)
    words     OS ("win" | "mac")  WORDS {key: user-visible word}  MODEL_NOTES {speech model key: note}
"""
import sys

API = (
    "OS", "WORDS", "MODEL_NOTES", "WEBVIEW_GUI", "DEFAULT_SPEAKER", "mic_privacy_url",
    "output_devices", "input_devices", "loopback_stream", "mic_stream",
    "prepare_gpu", "compute_device", "auto_speech", "gpu_ready", "system_info", "gpu_name",
    "copy_text", "open_path", "register_app", "app_icon",
    "overlay_stealth", "apply_invisible", "set_click_through", "click_through",
    "protect", "unprotect",
    "config_dir", "log_dir", "documents_folder", "needs_log_file",
    "start_recover_hotkey",
    "single_instance", "bring_to_front",
)

if sys.platform == "darwin":
    from . import mac as _impl
else:
    from . import windows as _impl

globals().update({name: getattr(_impl, name) for name in API})

__all__ = list(API)
