param([switch]$Release, [switch]$LocalBuild)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
# Rust canonical paths use verbatim prefixes, which Windows PowerShell 5.1
# cannot pass through its provider-based Join-Path / Split-Path operations.
$taskScriptRoot = $PSScriptRoot
if ($taskScriptRoot.StartsWith('\\?\UNC\')) {
    $taskScriptRoot = '\\' + $taskScriptRoot.Substring(8)
} elseif ($taskScriptRoot.StartsWith('\\?\')) {
    $taskScriptRoot = $taskScriptRoot.Substring(4)
}
$projectRoot = Split-Path $taskScriptRoot -Parent
if (-not $LocalBuild) {
    # Compatibility entry point for old desktop versions, without local Rust.
    $taskPython = @('runtime/upstream/toolkit/python.exe', 'toolkit/python.exe') |
        ForEach-Object { Join-Path $projectRoot $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $taskPython) { throw 'Bundled Python runtime is missing.' }
    $taskGit = Join-Path (Split-Path $taskPython -Parent) 'Git/cmd/git.exe'
    if (-not (Test-Path -LiteralPath $taskGit)) { $taskGit = (Get-Command git -ErrorAction Stop).Source }
    $taskCommit = & $taskGit -C $projectRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the installed source revision.' }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    & $taskPython (Join-Path $projectRoot 'scripts/install_prebuilt.py') --root $projectRoot --commit $taskCommit
    if ($LASTEXITCODE -ne 0) { throw 'Prebuilt program update failed; the current executable is preserved.' }
    return
}
Push-Location $projectRoot
$taskTranscriptStarted = $false
try {
    $taskLogDir = Join-Path $projectRoot 'runtime'
    New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
    Start-Transcript -Path (Join-Path $taskLogDir 'build-latest.log') -Force | Out-Null
    $taskTranscriptStarted = $true
    if (Test-Path '.build-tools/cargo/bin/cargo.exe') {
        $env:RUSTUP_HOME = Join-Path $projectRoot '.build-tools/rustup'
        $env:CARGO_HOME = Join-Path $projectRoot '.build-tools/cargo'
        $env:PATH = "$projectRoot\.build-tools\cargo\bin;$env:PATH"
        $taskSysroot = & rustc --print sysroot
        if ($LASTEXITCODE -ne 0) { throw 'Cannot locate Rust toolchain.' }
        $taskLlvmBin = Join-Path $taskSysroot 'lib/rustlib/x86_64-pc-windows-gnu/bin'
        $env:PATH = "$projectRoot\.build-tools\cargo\bin;$taskLlvmBin;$env:PATH"
        # The GNU distribution includes its system libraries and LLVM linker.
        $env:RUSTFLAGS = '-C linker=rust-lld -C linker-flavor=ld.lld -C link-self-contained=yes'
    }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Rust toolchain is missing. Restore the bundled .build-tools directory.' }
    if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) {
        foreach ($taskNativeCompiler in @((Join-Path $projectRoot '.build-tools/w64devkit/bin'), 'C:/msys64/mingw64/bin', 'C:/msys64/ucrt64/bin')) {
            if (Test-Path -LiteralPath (Join-Path $taskNativeCompiler 'gcc.exe')) {
                $env:PATH = "$taskNativeCompiler;$env:PATH"
                break
            }
        }
    }
    if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) { throw 'Native OCR compilation requires MinGW GCC. Install it in .build-tools/w64devkit or add gcc to PATH. End users can use precompiled updates.' }
    $buildArgs = @('build', '--locked')
    if ($Release) { $buildArgs += '--release' }
    $taskCargoOut = Join-Path $taskLogDir 'build-cargo.stdout.log'
    $taskCargoErr = Join-Path $taskLogDir 'build-cargo.stderr.log'
    $taskCargo = Start-Process -FilePath (Get-Command cargo).Source -ArgumentList $buildArgs `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $taskCargoOut -RedirectStandardError $taskCargoErr
    foreach ($taskOutput in @($taskCargoOut, $taskCargoErr)) {
        Get-Content -LiteralPath $taskOutput -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    $global:LASTEXITCODE = $taskCargo.ExitCode
    if ($LASTEXITCODE -ne 0) { throw 'Rust compilation failed. See runtime/build-latest.log.' }
    $mode = if ($Release) { 'release' } else { 'debug' }
    $taskBuiltExe = Join-Path $projectRoot "target/$mode/GanganMouse.exe"
    $taskInstalledExe = Join-Path $projectRoot 'GanganMouse.exe'
    $taskPreviousDir = Join-Path $projectRoot '.build-tools/previous-executables'
    New-Item -ItemType Directory -Path $taskPreviousDir -Force | Out-Null
    $taskPendingExe = Join-Path $taskPreviousDir ([System.IO.Path]::GetRandomFileName() + '.exe')
    $taskPreviousExe = Join-Path $taskPreviousDir ([System.IO.Path]::GetRandomFileName() + '.exe')
    Copy-Item -LiteralPath $taskBuiltExe -Destination $taskPendingExe -ErrorAction Stop
    # Stage the complete file before replacing it. Renaming also works for a running EXE.
    $taskHadPrevious = Test-Path -LiteralPath $taskInstalledExe
    if ($taskHadPrevious) {
        Move-Item -LiteralPath $taskInstalledExe -Destination $taskPreviousExe -Force -ErrorAction Stop
    }
    try {
        Move-Item -LiteralPath $taskPendingExe -Destination $taskInstalledExe -ErrorAction Stop
    } catch {
        if ($taskHadPrevious) {
            Move-Item -LiteralPath $taskPreviousExe -Destination $taskInstalledExe -Force -ErrorAction Stop
        }
        throw
    }
    Write-Host "Build completed: $projectRoot\GanganMouse.exe"
} catch {
    Write-Host ($_ | Out-String)
    throw
} finally {
    if ($taskTranscriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
}
