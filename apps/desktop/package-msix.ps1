<#
.SYNOPSIS
  Packs the built SelekyT Windows application into an MSIX package.

.DESCRIPTION
  This is the Store / sideload half of the distribution contract; the NSIS and
  MSI installers are the direct-download half, produced by `tauri build`.

  The payload is not assembled by hand. The script runs the NSIS installer that
  `tauri build --bundles nsis` already produced, silently, into an empty
  directory, and packs *that* directory. This matters because the alternative —
  reconstructing where Tauri places the sidecar binaries, the bundled Python
  interpreter, ffmpeg and the whisper weights — is a layout that can only be
  observed on Windows. Getting it wrong would ship an MSIX whose payload does
  not match what the installer installs. Installing and re-packing makes the two
  distribution paths provably contain the same files.

  Everything in this script is UNVERIFIED on this host: there is no MakeAppx,
  no Windows SDK and no PowerShell here, so it has never run. It is written to
  fail loudly at each step and to print what it looked for when it does, so the
  first Windows run is the verification rather than a guess.

.PARAMETER BundleDir
  The directory containing the NSIS installer produced by tauri build
  (src-tauri/target/release/bundle/nsis/*.exe).

.PARAMETER OutDir
  Where to write the resulting .msix.

.PARAMETER SkipSigning
  The MSIX is left unsigned. Signing is a separate release stage that requires
  a certificate whose subject matches the manifest's Publisher exactly. An
  unsigned MSIX will not install without developer mode or sideloading enabled,
  and this script does not pretend otherwise.

.EXAMPLE
  .\package-msix.ps1 -BundleDir src-tauri\target\release\bundle\nsis `
    -OutDir src-tauri\target\release\bundle\msix
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundleDir,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    [switch]$SkipSigning
)

$ErrorActionPreference = "Stop"

# ── locate the NSIS installer ────────────────────────────────────────────────

# -Recurse rather than a flat lookup: actions/download-artifact reproduces the
# tree the upload captured unless merge-multiple is set, so the installer can
# arrive several directories deep. Searching for it costs nothing and keeps the
# script honest about either layout.
$installer = Get-ChildItem -Path $BundleDir -Recurse -Filter "*.exe" -File |
    Sort-Object Length -Descending |
    Select-Object -First 1

if (-not $installer) {
    $found = (Get-ChildItem -Path $BundleDir -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object -First 10 -ExpandProperty FullName) -join "`n  "
    throw "No NSIS installer (.exe) found under '$BundleDir'. Run 'npx tauri build --bundles nsis' first; this script packs what that produced.`n  first files present:`n  $found"
}
Write-Host "packing payload from installer: $($installer.Name) ($('{0:N1}' -f ($installer.Length / 1MB)) MB)"

# ── install it silently into an empty directory ──────────────────────────────

$payload = Join-Path $env:TEMP "selekyt-msix-payload"
if (Test-Path $payload) {
    # A leftover payload directory would silently mix two builds' files.
    Remove-Item $payload -Recurse -Force
}
New-Item -ItemType Directory -Path $payload -Force | Out-Null

Write-Host "installing silently into $payload"
# /S is NSIS's silent flag; /D sets the install directory and must be last and
# unquoted-path-form. The installer is built with installMode currentUser, so
# no elevation is needed.
$install = Start-Process -FilePath $installer.FullName -ArgumentList "/S", "/D=$payload" -Wait -PassThru -NoNewWindow
if ($install.ExitCode -ne 0) {
    throw "The silent install exited with code $($install.ExitCode). The payload cannot be trusted if the installer did not complete."
}


# ── place the manifest and assets at the payload root ────────────────────────

# The script lives at apps/desktop/ and the manifest under src-tauri/msix/.
# $PSScriptRoot is the PowerShell 3+ way to get this directory and is what the
# pwsh shell GitHub uses resolves; $MyInvocation.MyCommand.Path is the pre-v3
# idiom and can come back empty when the script is dot-sourced — which is how
# `shell: pwsh` actually invokes it, as `pwsh -Command ". 'script.ps1'"`. Both
# are tried because the fallback costs nothing and a $null here would fail four
# lines later at the manifest copy with a message that does not name the cause.
$here = if ($MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} elseif ($PSScriptRoot) {
    $PSScriptRoot
} else {
    throw "Neither $MyInvocation.MyCommand.Path nor $PSScriptRoot resolved; the script cannot locate the manifest beside itself."
}
# $here IS the directory whose child is src-tauri. Do not go a level higher:
# this script lives at apps/desktop/, and the manifest is at
# apps/desktop/src-tauri/msix/, so splitting the parent resolves to apps/ and
# the manifest check below reports a path that never existed. That is what run
# 35549741215 failed on — the message named "apps\src-tauri\msix\AppxManifest.xml",
# one directory short of the real file, and it read like a missing manifest
# rather than like a wrong anchor.
$tauri = $here

$manifestSrc = Join-Path $tauri "src-tauri\msix\AppxManifest.xml"
if (-not (Test-Path $manifestSrc)) {
    throw "AppxManifest.xml is missing at $manifestSrc"
}
$assetsSrc = Join-Path $tauri "src-tauri\msix\Assets"
if (-not (Test-Path $assetsSrc)) {
    throw "The MSIX asset directory is missing at $assetsSrc (regenerate with icons\make_icons.py)"
}

$payloadManifest = Join-Path $payload "AppxManifest.xml"
Copy-Item $manifestSrc $payloadManifest -Force
$assetsDst = Join-Path $payload "Assets"
if (Test-Path $assetsDst) { Remove-Item $assetsDst -Recurse -Force }
Copy-Item $assetsSrc $assetsDst -Recurse -Force

# ── reconcile the executable path against the installed payload ─────────────

$exe = Get-ChildItem -Path $payload -Recurse -Filter "SelekyT.exe" -File |
    # Prefer the shallowest match: the application executable sits at the
    # install root, while a bundled Python tree could contain a same-named shim.
    Sort-Object { ($_.FullName.Substring($payload.Length) -split '\\').Length } |
    Select-Object -First 1
if (-not $exe) {
    $found = (Get-ChildItem -Path $payload -Recurse -File |
        Select-Object -First 10 -ExpandProperty FullName) -join "`n  "
    throw "SelekyT.exe is not in the installed payload. The manifest's Executable would point at nothing.`n  first files found:`n  $found"
}

# The manifest's Executable is package-relative, and the layout Tauri's NSIS
# installer produces can only be observed on Windows — not on the host that
# maintains this manifest. So do not trust the committed value: reconcile it
# against the payload that was just installed, repair the copy being packed,
# and report the disagreement loudly. A silent repair would hide real drift; a
# hard failure would block the build on a fact nobody could have checked.
$relative = $exe.FullName.Substring($payload.Length + 1)
$manifestText = Get-Content $payloadManifest -Raw
$declared = [regex]::Match($manifestText, 'Executable="([^"]+)"').Groups[1].Value
if ($declared -ne $relative) {
    Write-Warning "The manifest declares Executable='$declared' but the installed payload places it at '$relative'. The packed manifest is being corrected to match the payload; update the committed manifest so they agree."
    $manifestText = $manifestText -replace ('Executable="' + [regex]::Escape($declared) + '"'), ('Executable="' + $relative + '"')
    Set-Content -Path $payloadManifest -Value $manifestText -NoNewline -Encoding UTF8
}
Write-Host "payload executable: $relative"

# MakeAppx fails the pack if the manifest references an image that is not in
# the payload, so check it here with a message that names the missing file
# rather than letting a bare error code speak.
#
# Two syntactic shapes carry an image path: the Logo=/Image= attributes on the
# tile and splash elements, and the <Logo> element text under <Properties>,
# which is the store logo. Matching only the attributes silently skips that one
# and then reports "all referenced assets present" about a set it did not fully
# read — the store logo is the asset MakeAppx looks for earliest. The same
# expression lives in tests/test_desktop_packaging.py::_asset_references, which
# is the pin this loop is implementing.
$referenced = @(
    [regex]::Matches($manifestText, '(?i)(?:Logo|Image)="([^"]+\.(?:png|jpg|jpeg))"') |
        ForEach-Object { $_.Groups[1].Value }
    [regex]::Matches($manifestText, '(?i)<Logo>\s*([^<]+\.(?:png|jpg|jpeg))\s*</Logo>') |
        ForEach-Object { $_.Groups[1].Value }
) | Sort-Object -Unique
if ($referenced.Count -eq 0) {
    # An empty match set makes the loop below pass vacuously, which is worse
    # than not checking at all: a manifest that lost every image reference would
    # pack a package with no Start Menu presence and a green log.
    throw "No image references were found in the manifest at $payloadManifest. The asset check below is vacuous without them, so this is a failure rather than a pass."
}
foreach ($rel in $referenced) {
    $resolved = Join-Path $payload $rel
    if (-not (Test-Path $resolved)) {
        throw "The manifest references '$rel' but it is not in the payload at $resolved. Either the asset was not generated or the manifest and the generator drifted apart."
    }
}
Write-Host "all $($referenced.Count) manifest-referenced assets are present in the payload"

# ── locate MakeAppx ───────────────────────────────────────────────────────────

$makeappx = Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" -Recurse -Filter "makeappx.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $makeappx) {
    throw "MakeAppx.exe was not found under C:\Program Files (x86)\Windows Kits\10\bin. Install the Windows SDK, or the 'MSIX packaging' workload."
}
Write-Host "using MakeAppx: $($makeappx.FullName)"

# ── pack ─────────────────────────────────────────────────────────────────────

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$msix = Join-Path $OutDir "SelekyT.msix"

Write-Host "packing $msix"
# Validation is deliberately ON: it is the only check of the manifest's schema
# that exists, and this manifest has never been validated. If it fails, the
# failure is the point — read it, fix the manifest, run again.
& $makeappx.FullName pack -d $payload -p $msix -v
if ($LASTEXITCODE -ne 0) {
    throw "MakeAppx pack failed with exit code $LASTEXITCODE. See the output above; this is the schema validation this script cannot perform anywhere else."
}

$packed = Get-Item $msix
Write-Host "wrote $($packed.Name) ($('{0:N1}' -f ($packed.Length / 1MB)) MB) from $((Get-ChildItem $payload -Recurse -File).Count) files"

if (-not $SkipSigning) {
    Write-Warning "This MSIX is UNSIGNED. It will not install without developer mode or sideloading, and Windows will report it as an untrusted package. Signing is a separate release stage that requires a certificate whose subject matches 'CN=SelekyT Project' exactly."
}
Write-Host "done."
