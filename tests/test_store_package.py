"""Microsoft Store (MSIX) package: the program folder is read-only, so the GPU add-on lives in
%LOCALAPPDATA%\\Dolmi\\gpu; the Store updates Dolmi, so its own update check is off. The Inno install
keeps {app}\\gpu and its update check. Nothing here downloads or touches the real folders."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import dolmi_platform
from dolmi_platform import mac, windows
from ui import app as app_mod

ROOT = Path(__file__).resolve().parent.parent
MSIX = ROOT / "installer" / "msix"


@pytest.fixture
def packaged(monkeypatch):
    def set_to(on):
        monkeypatch.setattr(windows, "_packaged", on)
    return set_to


@pytest.fixture
def local_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return tmp_path / "Local"


def test_a_test_run_is_not_packaged():
    windows._packaged = None
    assert windows.is_packaged() is False
    assert mac.is_packaged() is False


def test_inno_install_keeps_the_gpu_folder_next_to_the_runtime(packaged, monkeypatch, tmp_path):
    packaged(False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "Dolmi" / "runtime"))
    assert windows.gpu_dir() == tmp_path / "Dolmi" / "gpu"       # python312._pth lists ..\gpu


def test_store_package_puts_the_gpu_add_on_in_local_appdata(packaged, local_appdata):
    packaged(True)
    assert windows.gpu_dir() == local_appdata / "Dolmi" / "gpu"


def test_settings_and_logs_stay_where_existing_users_have_them(packaged, local_appdata, monkeypatch, tmp_path):
    """Same folders packaged or not, so a Store install finds an Inno user's settings and vocabulary."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    for on in (False, True):
        packaged(on)
        assert windows.config_dir() == tmp_path / "Roaming" / "Dolmi"
        assert windows.log_dir() == local_appdata / "Dolmi"


def test_gpu_add_on_command_targets_the_given_folder_with_the_installers_packages(tmp_path):
    cmd = windows.gpu_addon_command(tmp_path / "gpu")
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert cmd[cmd.index("--target") + 1] == str(tmp_path / "gpu")
    assert "--no-cache-dir" in cmd                                # no second 1.3 GB copy in pip's cache
    assert cmd[-2:] == ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10"]
    iss = (ROOT / "installer" / "dolmi.iss").read_text(encoding="utf-8")
    assert "nvidia-cublas-cu12" in iss and "nvidia-cudnn-cu12>=9,<10" in iss   # one add-on, both routes


def test_prepare_gpu_finds_the_add_on_in_local_appdata(packaged, local_appdata, monkeypatch):
    packaged(True)
    bin_dir = local_appdata / "Dolmi" / "gpu" / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    added = []
    monkeypatch.setattr(windows.os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "path", list(sys.path))
    windows.prepare_gpu()
    assert str(bin_dir) in added
    assert str(local_appdata / "Dolmi" / "gpu") in sys.path


def test_install_gpu_addon_runs_pip_into_local_appdata(packaged, local_appdata, monkeypatch):
    packaged(True)
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or Done())
    monkeypatch.setattr(windows, "prepare_gpu", lambda: None)
    ok, _ = windows.install_gpu_addon()
    assert ok
    assert ran[0][ran[0].index("--target") + 1] == str(local_appdata / "Dolmi" / "gpu")


def test_install_gpu_addon_reports_a_failed_download(packaged, local_appdata, monkeypatch):
    packaged(True)

    class Failed:
        returncode, stdout, stderr = 1, "", "network down"

    monkeypatch.setattr(windows.subprocess, "run", lambda cmd, **kw: Failed())
    ok, message = windows.install_gpu_addon()
    assert not ok and "failed" in message


def test_store_package_has_no_own_update_check(api, packaged, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "is_packaged", lambda: True)
    monkeypatch.setattr(app_mod.updates, "check", lambda *a, **k: pytest.fail("asked GitHub"))
    r = api.check_update(force=True)
    assert r["available"] is False and r["store"] is True
    assert api.state()["packaged"] is True
    started = []
    monkeypatch.setattr(app_mod.threading, "Thread", lambda *a, **k: started.append(k) or pytest.fail("thread"))
    api._start_update_check()
    assert started == []


def test_inno_install_still_checks_for_updates(api, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "is_packaged", lambda: False)
    monkeypatch.setattr(app_mod.updates, "check", lambda current: {"available": False, "version": "9.9.9",
                                                                     "url": "", "current": current})
    assert api.check_update(force=True)["version"] == "9.9.9"
    assert api.state()["packaged"] is False


def test_gpu_add_on_button_is_store_only(api, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "is_packaged", lambda: False)
    monkeypatch.setattr(dolmi_platform, "install_gpu_addon", lambda: pytest.fail("installed outside the Store"))
    assert api.install_gpu_addon()["ok"] is False


# ------------------------------------------------------------------ the manifest template
NS = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
      "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
      "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
      "virtualization": "http://schemas.microsoft.com/appx/manifest/virtualization/windows10"}


def manifest():
    return ET.parse(MSIX / "AppxManifest.xml").getroot()


def test_manifest_starts_the_bundled_pythonw_without_a_working_directory():
    app = manifest().find("m:Applications/m:Application", NS)
    assert app.get("Executable") == "runtime\\pythonw.exe"
    # -m dolmi_launch: found through python312._pth ("."), so it works whatever the current folder is
    assert app.get("{%s}Parameters" % NS["uap10"]) == "-m dolmi_launch"
    assert app.get("EntryPoint") == "Windows.FullTrustApplication"


def test_manifest_asks_only_for_what_dolmi_needs():
    caps = manifest().find("m:Capabilities", NS)
    rescap = sorted(c.get("Name") for c in caps.findall("rescap:Capability", NS))
    devices = sorted(c.get("Name") for c in caps.findall("m:DeviceCapability", NS))
    assert rescap == ["runFullTrust", "unvirtualizedResources"]
    assert devices == ["microphone"]


def test_manifest_keeps_dolmis_appdata_folders_shared_with_the_inno_install():
    excluded = [e.text for e in manifest().iter("{%s}ExcludedDirectory" % NS["virtualization"])]
    assert excluded == ["$(KnownFolder:RoamingAppData)\\Dolmi", "$(KnownFolder:LocalAppData)\\Dolmi"]


def test_identity_placeholders_live_only_in_store_config():
    text = (MSIX / "AppxManifest.xml").read_text(encoding="utf-8")
    for token in ("{{IDENTITY_NAME}}", "{{PUBLISHER}}", "{{PUBLISHER_DISPLAY_NAME}}", "{{VERSION}}"):
        assert token in text
    cfg = (MSIX / "store-config.psd1").read_text(encoding="utf-8")
    assert "IdentityName" in cfg and "Publisher" in cfg and "PublisherDisplayName" in cfg


def test_launcher_runs_main_from_the_app_folder():
    src = (MSIX / "dolmi_launch.py").read_text(encoding="utf-8")
    assert '"app" / "src" / "main.py"' in src and "run_path" in src
