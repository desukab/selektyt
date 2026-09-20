<#
.SYNOPSIS
  SelekyT Windows code-signing hook, invoked by `tauri build` via the
  `bundle.windows.signCommand` entry in tauri.conf.json.

.DESCRIPTION
  Tauri replaces "%1" in the configured args with the path of the binary it
  just built and runs this script; signing every shipped binary is what turns
  the installer's "Unknown publisher" into a real identity.

  The trust state is deliberately *conditional*, and the two outcomes are not
  equivalent:

    * If the signing credentials are absent, the build still completes and
      ships, but the binary is UNSIGNED. That is reported loudly on the
      console and in the build log, and the release process is expected to
      carry the word "unsigned" with it. An unsigned installer is not broken,
      but Windows SmartScreen will warn about it, and that warning is not a
      bug to be hidden.

    * If the credentials ARE present and signing then fails, the script exits
      non-zero and the build fails. Silently shipping an unsigned build under
      a signing configuration would be worse than failing loudly.

  Which means: do not set the variables below until a real credential exists.

.CREDENTIAL CONTRACT
  All five are required together. They are the Azure Trusted Signing / key
  vault parameters; set them as repository or environment secrets in the
  release workflow, never in this file.

    SELEKYT_AZURE_TENANT_ID      Directory (tenant) id of the service principal
    SELEKYT_AZURE_CLIENT_ID      Application (client) id
    SELEKYT_AZURE_CLIENT_SECRET  The principal's secret
    SELEKYT_AZURE_KEY_VAULT_URI  e.g. https://selekyt.vault.azure.net
    SELEKYT_AZURE_CERT_NAME      The signing certificate's name in the vault

  SELEKYT_SIGN_METHOD  azuresigntool (default) | signtool
    azuresigntool is the community client for Azure Trusted Signing.
    signtool is the Windows SDK signer, used with a local certificate store
    thumbprint when a certificate is installed on the machine instead.

  SELEKYT_CERT_THUMBPRINT  Required only with SELEKYT_SIGN_METHOD=signtool.

  SELEKYT_SKIP_SIGNING=1   Force-skip; takes precedence over everything. Used
    by the unsigned release path so the same config builds both artifacts.

.NOTES
  UNVERIFIED on Linux: this environment has no Windows SDK or signing client,
  so the script has never been executed. Its behaviour is specified by the
  tests in tests/test_signing_contract.py, which assert the contract above
  (skip precedence, required-variable set, unsigned exit code) without
  needing Windows. Verify on a Windows runner with a real credential before
  publishing.
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$BinaryPath
)

$ErrorActionPreference = "Stop"

function Write-Unsigned([string]$Reason) {
  # Two deliberately plain markers: a human line and a machine-greppable one.
  # The CI release step looks for SELEKYT_UNSIGNED so the artifact name and
  # the release notes cannot drift from the truth.
  Write-Host "selekyt: NOT SIGNING - $Reason" -ForegroundColor Yellow
  Write-Host "##[warning]SELEKYT_UNSIGNED"
}

# 1. An explicit skip always wins, so the unsigned release path can reuse the
#    identical config and still produce an honestly-labelled artifact.
if ($env:SELEKYT_SKIP_SIGNING -eq "1") {
  Write-Unsigned "SELEKYT_SKIP_SIGNING=1 was set"
  exit 0
}

if (-not (Test-Path -LiteralPath $BinaryPath)) {
  # Nothing to sign is not a signing failure, but it should never happen
  # either; report it rather than exit 0 and look successful.
  Write-Error "the binary to sign does not exist: $BinaryPath"
  exit 2
}

# 2. The Azure Trusted Signing parameter set. All five are required: a partial
#    set would produce a different error from each client and the failure would
#    not be attributable.
$azure = @{
  TenantId     = $env:SELEKYT_AZURE_TENANT_ID
  ClientId     = $env:SELEKYT_AZURE_CLIENT_ID
  ClientSecret = $env:SELEKYT_AZURE_CLIENT_SECRET
  VaultUri     = $env:SELEKYT_AZURE_KEY_VAULT_URI
  CertName     = $env:SELEKYT_AZURE_CERT_NAME
}
$method = if ($env:SELEKYT_SIGN_METHOD) { $env:SELEKYT_SIGN_METHOD } else { "azuresigntool" }

if ($method -eq "signtool") {
  if ([string]::IsNullOrWhiteSpace($env:SELEKYT_CERT_THUMBPRINT)) {
    Write-Unsigned "SELEKYT_SIGN_METHOD=signtool but SELEKYT_CERT_THUMBPRINT is empty"
    exit 0
  }
} else {
  $missing = @($azure.GetEnumerator() | Where-Object { [string]::IsNullOrWhiteSpace($_.Value) } |
    ForEach-Object { $_.Key })
  if ($missing.Count -gt 0) {
    Write-Unsigned "no signing credentials configured (missing: $($missing -join ', '))"
    Write-Host "selekyt: set SELEKYT_AZURE_* secrets to sign, or SELEKYT_SKIP_SIGNING=1 to silence this."
    exit 0
  }
}

# 3. Really sign. From here on a failure must fail the build.
#    Azure Trusted Signing timestamps with RFC 3161 (the /tr flag), which is
#    why the timestamp URL is passed as a URL rather than a file/URL pair.
$timestampUrl = if ($env:SELEKYT_TIMESTAMP_URL) { $env:SELEKYT_TIMESTAMP_URL } else { "http://timestamp.acs.microsoft.com" }

try {
  if ($method -eq "signtool") {
    $thumb = $env:SELEKYT_CERT_THUMBPRINT
    Write-Host "selekyt: signing $BinaryPath (signtool, sha256, thumbprint $($thumb.Substring(0,8))...)"
    & signtool.exe sign /fd SHA256 /sha1 $thumb /tr $timestampUrl /td SHA256 $BinaryPath
  } else {
    Write-Host "selekyt: signing $BinaryPath (azuresigntool, sha256, cert $($azure.CertName))"
    & azuresigntool.exe sign `
      -fd sha256 `
      -tr $timestampUrl `
      -td sha256 `
      -kvu $azure.VaultUri `
      -kvi $azure.ClientId `
      -kvt $azure.TenantId `
      -kvs $azure.ClientSecret `
      -kvc $azure.CertName `
      $BinaryPath
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Error "the signing client exited with code $LASTEXITCODE; refusing to ship an unsigned binary under a signing configuration"
    exit $LASTEXITCODE
  }
  # The counter-check: say it is signed only because the tool said so.
  & signtool.exe verify /pa $BinaryPath
  if ($LASTEXITCODE -ne 0) {
    Write-Error "the signature did not verify after signing (signtool verify /pa)"
    exit $LASTEXITCODE
  }
  Write-Host "selekyt: SIGNED and verified: $BinaryPath" -ForegroundColor Green
} catch {
  Write-Error "signing failed: $_"
  exit 1
}
