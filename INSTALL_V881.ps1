$ErrorActionPreference = "Stop"
try {
    $aiFolder = ([char]0x0418).ToString() + ([char]0x0418).ToString()
    $targetRoot = Join-Path -Path "D:\Work" -ChildPath $aiFolder
    $projectRoot = Join-Path -Path $targetRoot -ChildPath "MG"
    $sourceDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd("\")

    if (-not (Test-Path -LiteralPath $projectRoot -PathType Container)) {
        throw ("Project folder was not found: " + $projectRoot + ". No API request was started.")
    }

    # The workstation's active installation lives inside the MG project tree.
    # Older bundle installers also used a sibling directory under D:\Work\ИИ.
    # Update the installation that already exists; never create a parallel branch
    # when the in-project installation is present.
    $inProjectDir = Join-Path -Path $projectRoot -ChildPath "_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_8_0_SOURCE_SKY_RECOVERY"
    $siblingDir = Join-Path -Path $targetRoot -ChildPath "MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_8_0_SOURCE_SKY_RECOVERY"
    if (Test-Path -LiteralPath $inProjectDir -PathType Container) {
        $targetDir = $inProjectDir
    }
    elseif (Test-Path -LiteralPath $siblingDir -PathType Container) {
        $targetDir = $siblingDir
    }
    else {
        # Fresh install: keep the application self-contained inside MG.
        $targetDir = $inProjectDir
    }

    $previousCandidates = @(
        (Join-Path -Path $projectRoot -ChildPath "_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_7_9_SIBLING_DONOR_RECOVERY"),
        (Join-Path -Path $targetRoot -ChildPath "MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_7_9_SIBLING_DONOR_RECOVERY")
    )

    Write-Host ("V8.8.1 update target: " + $targetDir)
    Write-Host ("Project data root: " + $projectRoot)

    $selectedFolder = $null
    $configCandidates = @((Join-Path -Path $targetDir -ChildPath "config.json"))
    foreach ($previousDir in $previousCandidates) {
        $configCandidates += (Join-Path -Path $previousDir -ChildPath "config.json")
    }
    foreach ($existingConfig in $configCandidates) {
        if (Test-Path -LiteralPath $existingConfig -PathType Leaf) {
            $candidate = Get-Content -LiteralPath $existingConfig -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($candidate.source -and (Test-Path -LiteralPath $candidate.source -PathType Container)) {
                $selectedFolder = [string]$candidate.source
                break
            }
        }
    }

    $baseFolder = Join-Path -Path $projectRoot -ChildPath "base"
    $baseQueueState = Join-Path -Path $baseFolder -ChildPath "GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE\_diagnostics\SEQUENTIAL_GENERATIVE_REVIEW_STATE.json"
    if ($selectedFolder -and [StringComparer]::OrdinalIgnoreCase.Equals($selectedFolder, $projectRoot) -and
        (Test-Path -LiteralPath $baseQueueState -PathType Leaf)) {
        $unfinished = Get-Content -LiteralPath $baseQueueState -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($unfinished.active -and $unfinished.active.source -and
            [string]$unfinished.active.source -like ($baseFolder + "\*")) {
            $selectedFolder = $baseFolder
            Write-Host "Recovering the unfinished MG/base queue before the completed project-root queue."
        }
    }

    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($sourceDir, $targetDir)) {
        & robocopy.exe $sourceDir $targetDir /E /R:2 /W:1 /XD .venv .qa_venv __pycache__ /XF *.pyc
        if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit code $LASTEXITCODE." }
    }

    # Force-copy the critical relocation fix even if Windows file timestamps from
    # an extracted archive are unusual. Remove stale bytecode before verification.
    foreach ($criticalName in @(
        "path_relocation_self_test.py",
        "v8_safe_appearance.py",
        "scene_stability_profile.json",
        "web_review_server.py",
        "web_console_bootstrap.py",
        "quality_gate_reason_catalog.py",
        "viewer_inspection_self_test.py",
        "version_guard.py",
        "cloudflare_bridge.py",
        "00_START_CLOUDFLARE_BRIDGE.bat",
        "01_INSTALL.bat"
    )) {
        $criticalSource = Join-Path -Path $sourceDir -ChildPath $criticalName
        $criticalTarget = Join-Path -Path $targetDir -ChildPath $criticalName
        Copy-Item -LiteralPath $criticalSource -Destination $criticalTarget -Force
    }
    $webSourceDir = Join-Path -Path $sourceDir -ChildPath "web"
    $webTargetDir = Join-Path -Path $targetDir -ChildPath "web"
    New-Item -ItemType Directory -Path $webTargetDir -Force | Out-Null
    foreach ($webName in @("index.html", "app.js", "app.css", "favicon.svg")) {
        Copy-Item -LiteralPath (Join-Path $webSourceDir $webName) -Destination (Join-Path $webTargetDir $webName) -Force
    }
    $pycache = Join-Path -Path $targetDir -ChildPath "__pycache__"
    if (Test-Path -LiteralPath $pycache -PathType Container) {
        Remove-Item -LiteralPath $pycache -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Refuse to run if the old self-test is still installed. This makes a wrong
    # target immediately visible instead of producing the same misleading traceback.
    $installedSelfTest = Join-Path -Path $targetDir -ChildPath "path_relocation_self_test.py"
    $installedText = Get-Content -LiteralPath $installedSelfTest -Raw -Encoding UTF8
    if ($installedText -notmatch "PROJECT-ROOT / SOURCE-QUEUE RELOCATION SELF-TEST PASSED") {
        throw ("Relocation hotfix was not installed into the active component: " + $installedSelfTest)
    }

    if ($selectedFolder) {
        $configPath = Join-Path -Path $targetDir -ChildPath "config.json"
        $newConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $newConfig.source = $selectedFolder
        [System.IO.File]::WriteAllText($configPath, ($newConfig | ConvertTo-Json -Depth 64), [System.Text.UTF8Encoding]::new($false))
        Write-Host ("Source queue preserved: " + $selectedFolder)
    }

    $installScript = Join-Path -Path $targetDir -ChildPath "01_INSTALL.bat"
    & cmd.exe /D /C ('call "{0}" --no-pause' -f $installScript)
    if ($LASTEXITCODE -ne 0) { throw "V8.8.1 component installation failed." }

    $launcher = Join-Path -Path $targetDir -ChildPath "00_START_MG_WEB_REVIEW.bat"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path -Path $desktop -ChildPath "MG 4K Web Review.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcher
    $shortcut.WorkingDirectory = $targetDir
    $shortcut.Description = "MG 4K Scene Stability Profile V8.8.1"
    $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,67"
    $shortcut.Save()
    Start-Process -FilePath $launcher -WorkingDirectory $targetDir
    Write-Host "V8.8.1 INSTALLED. SCENE STABILITY WEB CONSOLE STARTING."
    Write-Host "Preferred address: http://127.0.0.1:8742 (automatic fallback: 8743-8752)"
    Write-Host ("Desktop shortcut: " + $shortcutPath)
    Write-Host "No paid generation was started by installation."
    exit 0
}
catch {
    Write-Host ("INSTALLER ERROR: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
