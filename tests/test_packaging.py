"""The Mac app spec and the Windows installer only list files that exist, ship the shared core, and
Mac/ holds nothing but Mac-only files (no second copy of the core)."""
import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = ROOT / "Mac"
WEB = ROOT / "src" / "ui" / "web"

# Mac/ may only hold these (everything else is the shared core at the repo root)
MAC_ONLY = {
    ".gitignore", "README.md", "requirements.txt", "requirements-lock.txt", "start.command",
    "assets/dolmi.icns", "audio/dolmi-audio.swift",
    "packaging/build.sh", "packaging/dolmi.spec", "packaging/launcher.py", "packaging/make-signing-identity.sh",
}
MAC_BUILD_OUTPUT = ("venv/", "build/", "dist/", "audio/dolmi-audio", ".DS_Store")   # git-ignored


def _spec_lists():
    tree = ast.parse((MAC / "packaging" / "dolmi.spec").read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", "")
            if name in ("APP_FILES", "APP_DIRS", "MAC_FILES"):
                out[name] = [ast.literal_eval(e) for e in node.value.elts]
    return out


def test_mac_spec_lists_only_existing_paths():
    lists = _spec_lists()
    assert set(lists) == {"APP_FILES", "APP_DIRS", "MAC_FILES"}
    assert [f for f in lists["APP_FILES"] if not (ROOT / f).is_file()] == []
    assert [d for d in lists["APP_DIRS"] if not (ROOT / d).is_dir()] == []
    # the audio helper is built by build.sh; its source must be there
    assert [f for f in lists["MAC_FILES"] if not ((MAC / f).is_file() or (MAC / f).with_suffix(".swift").is_file())] == []


def test_mac_spec_ships_the_shared_core():
    lists = _spec_lists()
    assert {"src", "dolmi_platform", "voice"} <= set(lists["APP_DIRS"])
    for f in ("live_subs.py", "models.py", "cloud_speech.py", "assistant.py", "inbox.py", "streaming.py", "clarify.py",
              "speakers.py", "VERSION"):
        assert f in lists["APP_FILES"], f
    spec = (MAC / "packaging" / "dolmi.spec").read_text(encoding="utf-8")
    assert 'VERSION = (ROOT / "VERSION")' in spec


def _installer_copies():
    ps = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
    block = re.search(r'foreach \(\$f in (.*?)\) \{', ps, re.S).group(1)
    files = re.findall(r'"([^"]+)"', block)
    joined = [p.replace("\\", "/") for p in re.findall(r'Join-Path \$root "([^"]+)"', ps)]
    # the build venv and the output folder are not part of the repo
    return ps, files, [p for p in joined if not p.startswith(("venv/", "dist"))]


def test_installer_copies_only_existing_paths():
    ps, files, joined = _installer_copies()
    assert files and [f for f in files if not (ROOT / f).is_file()] == []
    assert [p for p in joined if not (ROOT / p).exists()] == []


def test_installer_ships_and_checks_the_platform_layer():
    ps, files, joined = _installer_copies()
    assert "dolmi_platform" in joined and "src" in joined and "voice" in joined
    assert {"streaming.py", "clarify.py", "speakers.py"} <= set(files)
    assert "stealth.py" not in files
    assert re.search(r"import live_subs, models, assistant, inbox, dolmi_platform", ps)


def _mac_files():
    """The files git tracks under Mac/; without git, what is on disk minus build output. Untracked local
    leftovers (an old .env, Finder's .DS_Store files) are not part of the repo, so they don't count."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", "Mac"], capture_output=True,
                             text=True, check=True, timeout=30).stdout
        return sorted(line[len("Mac/"):] for line in out.splitlines() if line)
    except (OSError, subprocess.SubprocessError):
        return sorted(p.relative_to(MAC).as_posix() for p in MAC.rglob("*")
                      if p.is_file() and not p.relative_to(MAC).as_posix().startswith(MAC_BUILD_OUTPUT)
                      and "__pycache__" not in p.parts and p.name != ".DS_Store")


def test_mac_folder_holds_only_mac_files():
    assert [p for p in _mac_files() if p not in MAC_ONLY] == []


def test_one_version_file():
    assert (ROOT / "VERSION").is_file() and not (MAC / "VERSION").exists()


def test_page_loads_its_css_and_scripts_in_order():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="(js/[^"]+)"></script>', html)
    assert scripts == [f"js/{n}.js" for n in ("core", "live", "meetings", "assistant", "vocab", "settings", "translator", "onboarding", "heard", "a11y")]
    assert sorted(p.name for p in (WEB / "js").glob("*.js")) == sorted(Path(s).name for s in scripts)
    assert '<link href="css/app.css" rel="stylesheet">' in html and (WEB / "css" / "app.css").is_file()
    assert "<style>" not in html and "<script>" not in html          # no inline CSS/JS left


def _root_modules_needed():
    """Repo-root .py modules the app imports (src/, dolmi_platform/, voice/ and those modules themselves)."""
    root_mods = {p.stem for p in ROOT.glob("*.py")} - {"make_icon"}
    pat = re.compile(r'^\s*(?:import|from)\s+([A-Za-z_]\w*)', re.M)
    todo = [p for d in ("src", "dolmi_platform", "voice") for p in (ROOT / d).rglob("*.py")]
    seen = set()
    while todo:
        names = set(pat.findall(todo.pop().read_text(encoding="utf-8"))) & root_mods
        for n in names - seen:
            seen.add(n)
            todo.append(ROOT / f"{n}.py")
    return seen


def test_packages_ship_every_root_module_the_app_imports():
    # cloud_translate.py (QA-7) was imported by the app but missing from both packages
    need = {f"{m}.py" for m in _root_modules_needed()}
    _ps, files, _joined = _installer_copies()
    assert sorted(need - set(files)) == []
    assert sorted(need - set(_spec_lists()["APP_FILES"])) == []
