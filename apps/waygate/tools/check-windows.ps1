param([string]$Python = "python", [string]$DevEcoHome = "", [switch]$Build)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
Push-Location $Repo
try {
    & $Python apps/waygate/tools/check_project.py
    if ($LASTEXITCODE -ne 0) { throw "Project sanity check failed" }
    & $Python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
    if ($LASTEXITCODE -ne 0) { throw "Offline verification failed" }
    if ($Build) {
        if (!$DevEcoHome) { throw "Pass -DevEcoHome with the actual DevEco Studio installation directory" }
        $Node = Join-Path $DevEcoHome "tools/node/node.exe"
        $Hvigor = Join-Path $DevEcoHome "tools/hvigor/bin/hvigorw.js"
        if (!(Test-Path $Node) -or !(Test-Path $Hvigor)) {
            throw "Bundled Node/Hvigor path differs. Check DevEco Build output and update this script; do not install an unrelated npm hvigor."
        }
        Push-Location apps/waygate/harmony
        try {
            & $Node $Hvigor --mode module -p product=default -p module=entry@default -p buildMode=debug assembleHap --no-daemon
            if ($LASTEXITCODE -ne 0) { throw "DevEco HAP build failed" }
        } finally { Pop-Location }
    }
} finally { Pop-Location }
