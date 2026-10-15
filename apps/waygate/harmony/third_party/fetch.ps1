# Waygate native SSH module: pinned third-party sources (libssh2 + mbedTLS).
# The source trees are gitignored on purpose; run this once before the first
# native build so entry/src/main/cpp/CMakeLists.txt can find them.
#
#   powershell -ExecutionPolicy Bypass -File third_party/fetch.ps1 -Proxy http://127.0.0.1:21882
#
param([string]$Proxy = '')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$sources = @(
    [pscustomobject]@{
        Name = 'libssh2.tar.gz'
        Url = 'https://github.com/libssh2/libssh2/archive/refs/tags/libssh2-1.11.1.tar.gz'
        Sha256 = '82B35C61C78B475647BDC981A183C5B5AB0D979E1CAEE94186E8F9150F2B0D0D'
        Dir = 'libssh2-libssh2-1.11.1'
    },
    [pscustomobject]@{
        # Official release tarball: contains the framework/ sources that the
        # GitHub archive of the tag does not include.
        Name = 'mbedtls-release.tar.bz2'
        Url = 'https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.2/mbedtls-3.6.2.tar.bz2'
        Sha256 = '8B54FB9BCF4D5A7078028E0520ACDDEFB7900B3E66FEC7F7175BB5B7D85CCDCA'
        Dir = 'mbedtls-3.6.2'
    }
)

foreach ($item in $sources) {
    $archive = Join-Path $here $item.Name
    $target = Join-Path $here $item.Dir
    if (Test-Path (Join-Path $target 'CMakeLists.txt')) {
        Write-Host "already present: $($item.Dir)"
        continue
    }
    if ((Test-Path $archive) -and
        ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $item.Sha256)) {
        Remove-Item $archive -Force
    }
    if (-not (Test-Path $archive)) {
        Write-Host "downloading $($item.Name)"
        $request = @{ Uri = $item.Url; OutFile = $archive; TimeoutSec = 300; UseBasicParsing = $true }
        if ($Proxy) { $request.Proxy = $Proxy }
        Invoke-WebRequest @request
    }
    $hash = (Get-FileHash $archive -Algorithm SHA256).Hash
    if ($hash -ne $item.Sha256) {
        throw "sha256 mismatch for $($item.Name): got $hash"
    }
    if ($item.Name -like '*.tar.bz2') { tar -xjf $archive -C $here } else { tar -xzf $archive -C $here }
    if (-not (Test-Path (Join-Path $target 'CMakeLists.txt'))) {
        throw "extraction did not produce $($item.Dir)"
    }
    Write-Host "ready: $($item.Dir)"
}
