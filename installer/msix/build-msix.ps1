# Builds Dolmi as an MSIX package for the Microsoft Store from the same app folder the Inno installer
# ships (dist\Dolmi: embedded runtime + app, made by installer\build.ps1).
#
#   powershell -ExecutionPolicy Bypass -File installer\msix\build-msix.ps1 -Test     # local test package, signed
#   powershell -ExecutionPolicy Bypass -File installer\msix\build-msix.ps1           # Store upload (unsigned)
#
#   -Test       test identity from store-config.psd1, signed with a self-signed certificate in
#               CurrentUser\My (created on first use, exported to dist\msix\DolmiTest.cer)
#   -Rebuild    run installer\build.ps1 -SkipInstaller first (otherwise reuses dist\Dolmi when present)
#
# The Store build needs the Partner Center identity in store-config.psd1 and is left unsigned: the Store
# signs it. Tools: MakeAppx, MakePri and SignTool from the Windows SDK (Program Files (x86)\Windows
# Kits\10\bin\<ver>\x64) or from the Microsoft.Windows.SDK.BuildTools NuGet package unpacked into
# installer\.cache\sdk-buildtools\pkg.
param(
  [string]$Version,
  [string]$Python,
  [switch]$Test,
  [switch]$Rebuild
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$here = $PSScriptRoot
$installer = Split-Path -Parent $here
$root = Split-Path -Parent $installer
if (-not $Version) { $Version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim() }
if (-not $Python) {
  $venvPy = Join-Path $root "venv\Scripts\python.exe"
  $Python = if (Test-Path $venvPy) { $venvPy } else { "python" }
}
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "Dolmi"
$out = Join-Path $dist "msix"
$layout = Join-Path $out "layout"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Assert-Exit($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }
function Esc($s) { [System.Security.SecurityElement]::Escape($s) }

# The Store wants four numbers with the last one 0: 2.3.4 -> 2.3.4.0
$parts = @($Version.Split("-")[0].Split(".") | ForEach-Object { [int]$_ })
while ($parts.Count -lt 3) { $parts += 0 }
$msixVersion = "{0}.{1}.{2}.0" -f $parts[0], $parts[1], $parts[2]

Step "Tools: MakeAppx, MakePri, SignTool"
$toolDirs = @(
  Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64" -Directory -ErrorAction SilentlyContinue
  Get-ChildItem (Join-Path $installer ".cache\sdk-buildtools\pkg\bin\*\x64") -Directory -ErrorAction SilentlyContinue
) | Where-Object { Test-Path (Join-Path $_.FullName "makeappx.exe") } | Sort-Object { [version]($_.Parent.Name) } -Descending
if (-not $toolDirs) {
  throw @"
MakeAppx.exe not found. Either install the Windows SDK (admin):
    winget install -e --id Microsoft.WindowsSDK.10.0.26100
or unpack the Microsoft.Windows.SDK.BuildTools NuGet package (no admin) into installer\.cache\sdk-buildtools\pkg:
    https://www.nuget.org/packages/Microsoft.Windows.SDK.BuildTools
"@
}
$tools = $toolDirs[0].FullName
$makeappx = Join-Path $tools "makeappx.exe"
$makepri = Join-Path $tools "makepri.exe"
$signtool = Join-Path $tools "signtool.exe"
Write-Host "    $tools"

Step "Identity"
$cfg = Import-PowerShellDataFile (Join-Path $here "store-config.psd1")
if ($Test) {
  $id = @{ Name = $cfg.TestIdentityName; Publisher = $cfg.TestPublisher
           PublisherDisplayName = $cfg.TestPublisherDisplayName; DisplayName = $cfg.TestDisplayName }
} else {
  $id = @{ Name = $cfg.IdentityName; Publisher = $cfg.Publisher
           PublisherDisplayName = $cfg.PublisherDisplayName; DisplayName = $cfg.DisplayName }
  $missing = $id.GetEnumerator() | Where-Object { $_.Value -like "PASTE_*" } | ForEach-Object { $_.Key }
  if ($missing) { throw "Fill $($missing -join ', ') in installer\msix\store-config.psd1 from Partner Center (or build with -Test)" }
}
Write-Host ("    {0}  {1}  v{2}" -f $id.Name, $id.Publisher, $msixVersion)

if ($Rebuild -or -not (Test-Path (Join-Path $stage "runtime\pythonw.exe"))) {
  Step "App folder: installer\build.ps1 -SkipInstaller"
  & (Join-Path $installer "build.ps1") -Version $Version -Python $Python -SkipInstaller
}
$staged = (Get-Content (Join-Path $stage "VERSION") -Raw).Trim()
if ($staged -ne $Version) { throw "dist\Dolmi is version $staged, not ${Version}; run with -Rebuild" }

Step "Package layout: dist\msix\layout"
if (Test-Path $layout) { Remove-Item $layout -Recurse -Force }
New-Item -ItemType Directory -Force $layout | Out-Null
Copy-Item (Join-Path $stage "*") $layout -Recurse
Copy-Item (Join-Path $here "dolmi_launch.py") (Join-Path $layout "runtime")   # -m dolmi_launch (manifest)

# The certification kit flags archives and process-launching .exe files. None of these run in Dolmi:
# the libraries' own test folders, and the setuptools/distlib script launchers (the GPU add-on's
# pip install makes no scripts).
$site = Join-Path $layout "runtime\Lib\site-packages"
Get-ChildItem $site -Recurse -Directory -Filter "tests" | Where-Object { $_.FullName -match '\\(networkx|numpy)\\' } |
    Remove-Item -Recurse -Force
Get-ChildItem $site -Recurse -File -Include "*.gz", "*.bz2" | Where-Object { $_.FullName -match '\\(networkx|numpy)\\' } |
    Remove-Item -Force
Get-ChildItem (Join-Path $site "setuptools"), (Join-Path $site "pip\_vendor\distlib") -File -Filter "*.exe" -ErrorAction SilentlyContinue |
    Remove-Item -Force

# The package folder is read-only, and timestamp-checked .pyc files can go stale when the files' times
# change in the package, so Python would compile in memory on every start. Hash-based .pyc files that are
# never re-checked suit a package that cannot change.
$rtPy = Join-Path $layout "runtime\python.exe"
Get-ChildItem $layout -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
& $rtPy -W ignore -m compileall -q -f -j 0 --invalidation-mode unchecked-hash `
  (Join-Path $layout "runtime\Lib\site-packages") (Join-Path $layout "app") (Join-Path $layout "runtime\dolmi_launch.py") | Out-Null
Assert-Exit "compileall"

Step "Images and resources.pri"
$assets = Join-Path $layout "Assets"
& $Python (Join-Path $here "make_assets.py") $assets
Assert-Exit "make_assets.py"

$manifest = (Get-Content (Join-Path $here "AppxManifest.xml") -Raw)
$manifest = $manifest.Replace("{{IDENTITY_NAME}}", (Esc $id.Name)).Replace("{{PUBLISHER}}", (Esc $id.Publisher))
$manifest = $manifest.Replace("{{PUBLISHER_DISPLAY_NAME}}", (Esc $id.PublisherDisplayName)).Replace("{{DISPLAY_NAME}}", (Esc $id.DisplayName))
$manifest = $manifest.Replace("{{VERSION}}", $msixVersion)
if ($manifest -match "\{\{") { throw "AppxManifest.xml still has a {{placeholder}}" }
[IO.File]::WriteAllText((Join-Path $layout "AppxManifest.xml"), $manifest, (New-Object Text.UTF8Encoding $false))

# MakePri indexes only the images (a project folder with Assets + the manifest), not the whole runtime
$priSrc = Join-Path $out "pri-src"
if (Test-Path $priSrc) { Remove-Item $priSrc -Recurse -Force }
New-Item -ItemType Directory -Force $priSrc | Out-Null
Copy-Item $assets $priSrc -Recurse
Copy-Item (Join-Path $layout "AppxManifest.xml") $priSrc
$priCfg = Join-Path $out "priconfig.xml"
& $makepri createconfig /cf $priCfg /dq en-US /pv 10.0.0 /o | Out-Null
Assert-Exit "makepri createconfig"
# one .msix, not a bundle: keep every scale in a single resources.pri (no per-scale resource packs)
$cfgXml = [xml](Get-Content $priCfg -Raw)
$packaging = $cfgXml.SelectSingleNode("//packaging")
if ($packaging) { [void]$packaging.ParentNode.RemoveChild($packaging) }
$cfgXml.Save($priCfg)
& $makepri new /pr $priSrc /cf $priCfg /mn (Join-Path $priSrc "AppxManifest.xml") /of (Join-Path $layout "resources.pri") /o | Out-Null
Assert-Exit "makepri new"
Remove-Item $priSrc -Recurse -Force

Step "MakeAppx pack"
$name = if ($Test) { "Dolmi-$Version-test.msix" } else { "Dolmi-$Version.msix" }
$msix = Join-Path $out $name
& $makeappx pack /d $layout /p $msix /o | Out-Null
Assert-Exit "makeappx pack"

if ($Test) {
  Step "Sign with the self-signed test certificate (CurrentUser\My)"
  $cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $id.Publisher -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
  if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type Custom -Subject $id.Publisher -KeyUsage DigitalSignature `
      -FriendlyName "Dolmi MSIX test signing" -CertStoreLocation Cert:\CurrentUser\My `
      -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}") -NotAfter (Get-Date).AddYears(2)
  }
  $cer = Join-Path $out "DolmiTest.cer"
  Export-Certificate -Cert $cert -FilePath $cer | Out-Null
  & $signtool sign /fd SHA256 /sha1 $cert.Thumbprint /s My $msix | Out-Null
  Assert-Exit "signtool sign"
  Write-Host "    signed by $($cert.Subject) ($($cert.Thumbprint))"
  Write-Host ""
  Write-Host "To install the test package, trust its certificate once (admin PowerShell), then add it:" -ForegroundColor Yellow
  Write-Host "    Import-Certificate -FilePath `"$cer`" -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
  Write-Host "    Add-AppxPackage -Path `"$msix`""
}

$mb = [math]::Round((Get-Item $msix).Length / 1MB)
Write-Host ("    {0} ({1} MB)" -f $msix, $mb) -ForegroundColor Green
