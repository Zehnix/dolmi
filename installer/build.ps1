# Builds the Dolmi installer: a self-contained app folder (dist\Dolmi) that runs on the official
# embeddable CPython, then dist\Dolmi-Setup-<version>.exe via Inno Setup.
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1                 # version from VERSION
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 2.0.1 -Python C:\py312\python.exe
#
# -Python must be a 64-bit CPython 3.12 (it resolves the wheels); defaults to the repo venv.
param(
  [string]$Version,
  [string]$Python,
  [switch]$SkipInstaller      # stop after dist\Dolmi (no Inno Setup needed)
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
if (-not $Version) { $Version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim() }
if (-not $Python) {
  $venvPy = Join-Path $root "venv\Scripts\python.exe"
  $Python = if (Test-Path $venvPy) { $venvPy } else { "python" }
}

# Runtime: official embeddable CPython, pinned and checksum-verified. Its pythonw.exe is signed by the
# Python Software Foundation, which Smart App Control trusts; a PyInstaller .exe would be a new
# unsigned binary on every release and could be blocked outright.
$PyVersion = "3.12.10"
$PyZipSha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
$pyZipName = "python-$PyVersion-embed-amd64.zip"

$cache = Join-Path $PSScriptRoot ".cache"
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "Dolmi"
$runtime = Join-Path $stage "runtime"
$site = Join-Path $runtime "Lib\site-packages"
$appDir = Join-Path $stage "app"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Assert-Exit($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

Step "Dolmi $Version"
$pyInfo = & $Python -c "import sys, platform; print('%d.%d' % sys.version_info[:2], platform.machine())"
Assert-Exit "build Python check"
if ($pyInfo -ne "3.12 AMD64") { throw "Build Python must be 64-bit CPython 3.12 (got '$pyInfo' from $Python)" }

Step "Runtime: embeddable CPython $PyVersion"
New-Item -ItemType Directory -Force $cache | Out-Null
$pyZip = Join-Path $cache $pyZipName
if (-not (Test-Path $pyZip)) {
  Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/$pyZipName" -OutFile $pyZip
}
$hash = (Get-FileHash $pyZip -Algorithm SHA256).Hash
if ($hash -ne $PyZipSha256) { Remove-Item $pyZip; throw "$pyZipName checksum mismatch ($hash); deleted, re-run to download again" }

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $runtime, $site, $appDir | Out-Null
Expand-Archive $pyZip -DestinationPath $runtime

# python312._pth fixes sys.path for the embedded runtime: stdlib zip, site-packages, and the optional
# GPU add-on folder ({app}\gpu, filled by the installer's GPU task). main.py adds the app itself.
$pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
Set-Content $pth.FullName -Encoding ascii -Value @(
  ($pth.BaseName + ".zip"), ".", "Lib\site-packages", "..\gpu", "import site"
)

Step "Packages: requirements-lock.txt (exact versions, wheels preferred)"
& $Python -m pip install --disable-pip-version-check --no-warn-script-location --no-compile `
  --prefer-binary --target $site -r (Join-Path $root "requirements-lock.txt") pip
Assert-Exit "pip install"
Get-ChildItem $site -Directory -Filter "bin" | Remove-Item -Recurse -Force   # console-script shims, unused

Step "App files"
foreach ($f in "live_subs.py", "cloud_speech.py", "assistant.py", "inbox.py", "models.py", "speakers.py", "clarify.py",
               "vocabulary.example.txt", "glossary.example.txt") {
  Copy-Item (Join-Path $root $f) $appDir
}
Copy-Item (Join-Path $root "src") (Join-Path $appDir "src") -Recurse
Copy-Item (Join-Path $root "dolmi_platform") (Join-Path $appDir "dolmi_platform") -Recurse   # Windows/Mac layer (shared core)
Get-ChildItem $appDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $appDir "assets") | Out-Null
Copy-Item (Join-Path $root "assets\dolmi-2.ico"), (Join-Path $root "assets\dolmi.png") (Join-Path $appDir "assets")
Set-Content (Join-Path $appDir "VERSION") $Version -Encoding ascii     # what Settings shows
Set-Content (Join-Path $stage "VERSION") $Version -Encoding ascii
Copy-Item (Join-Path $root "LICENSE") (Join-Path $stage "LICENSE.txt")
Copy-Item (Join-Path $root "THIRD-PARTY-NOTICES.md") $stage

Step "Precompile (.pyc) so first start is fast and never writes into Program Files"
$rtPy = Join-Path $runtime "python.exe"
& $rtPy -m compileall -q -j 0 $site $appDir | Out-Null
Assert-Exit "compileall"

Step "Smoke test: the bundled runtime imports the whole app"
& $rtPy -c "import sys; sys.path[:0] = [r'$appDir', r'$appDir\src']; import webview, faster_whisper, ctranslate2, onnxruntime, av, pyaudiowpatch, numpy, sentencepiece, huggingface_hub, anthropic, openai; import live_subs, models, assistant, inbox, dolmi_platform; assert dolmi_platform.OS == 'win'; from ui import app; print('imports ok, Dolmi', app.VERSION)"
Assert-Exit "smoke test"
# speaker labels (beta): the voice model runs on the one bundled onnxruntime (no second copy) - one
# embedding when the model is already cached (the build itself never downloads it)
& $rtPy -c "import sys; sys.path[:0] = [r'$appDir']; import numpy as np, onnxruntime, speakers; ok = speakers.is_installed(); v = speakers.Embedder(speakers.model_path())(np.random.default_rng(0).standard_normal(32000).astype('float32') * 0.1) if ok else None; assert v is None or v.shape == (512,), v.shape; print('speaker embedding ok' if ok else 'speaker model not cached, embedding skipped;', 'onnxruntime', onnxruntime.__version__, onnxruntime.__file__)"
Assert-Exit "speaker model smoke test"
& $rtPy -m pip --version | Out-Null
Assert-Exit "bundled pip (needed by the GPU add-on)"

$mb = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "    dist\Dolmi: $mb MB"
if ($SkipInstaller) { return }

Step "Installer: Inno Setup"
$iscc = @(
  (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw "ISCC.exe not found. Install Inno Setup 6: winget install -e --id JRSoftware.InnoSetup" }
& $iscc /Q "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "dolmi.iss")
Assert-Exit "ISCC"

$setup = Join-Path $dist "Dolmi-Setup-$Version.exe"
$sum = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
Set-Content (Join-Path $dist "SHA256SUMS.txt") "$sum  Dolmi-Setup-$Version.exe" -Encoding ascii
Write-Host ("    {0} ({1} MB)" -f $setup, [math]::Round((Get-Item $setup).Length / 1MB)) -ForegroundColor Green
Write-Host "    sha256 $sum"
