$ErrorActionPreference = "Stop"

$Source = "D:\Work\ИИ\MG\MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_8_1_SCENE_STABILITY_PROFILE"
$Stage = "D:\Work\ИИ\MG\_SYNC_MASTER_LOCAL_V881"
$Repo = "https://github.com/mkontrakevich/4K_Upscaler.git"
$Branch = "master-local-v881"
$Snapshot = Join-Path $Stage "local-master"
$Manifest = Join-Path $Stage "LOCAL_MASTER_MANIFEST.sha256.txt"

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "MASTER folder not found: $Source"
}

if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}

git clone --branch $Branch --single-branch $Repo $Stage
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }

New-Item -ItemType Directory -Path $Snapshot -Force | Out-Null

$excludeDirs = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "_diagnostics",
    "_cloud_jobs",
    "GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE",
    "4K_GENERATIVE_FINAL",
    "GENERATIVE_REVIEW_REQUIRED",
    "02_NANO_BANANA_PRO_RAW",
    "output",
    "outputs"
)

$excludeFiles = @(
    ".env",
    ".env.*",
    "API.txt",
    "OPENROUTER_API_KEY.txt",
    "*secret*",
    "*token*.txt",
    "WEB_CONSOLE_ENDPOINT.json",
    "*.pyc"
)

$xd = @()
foreach ($d in $excludeDirs) { $xd += @("/XD", $d) }
$xf = @()
foreach ($f in $excludeFiles) { $xf += @("/XF", $f) }

& robocopy.exe $Source $Snapshot /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP @xd @xf
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

$files = Get-ChildItem -LiteralPath $Snapshot -File -Recurse | Sort-Object FullName
$rows = foreach ($file in $files) {
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $rel = $file.FullName.Substring($Snapshot.Length).TrimStart("\")
    "$hash  local-master/$($rel.Replace('\','/'))"
}
[System.IO.File]::WriteAllLines($Manifest, $rows, [System.Text.UTF8Encoding]::new($false))

Push-Location $Stage
try {
    git add -- local-master LOCAL_MASTER_MANIFEST.sha256.txt
    if ($LASTEXITCODE -ne 0) { throw "git add failed" }

    $pending = git status --porcelain
    if (-not $pending) {
        Write-Host "No changes: local master already matches branch snapshot."
        exit 0
    }

    git commit -m "Snapshot local V8.8.1 scene stability master"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed" }

    git push origin $Branch
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }

    Write-Host ""
    Write-Host "MASTER SNAPSHOT PUSHED SUCCESSFULLY"
    Write-Host "Branch: $Branch"
    Write-Host "Source remained read-only: $Source"
    Write-Host "Snapshot: $Snapshot"
    Write-Host "Manifest: $Manifest"
}
finally {
    Pop-Location
}
