param([switch]$Release)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$projectRoot = Split-Path $PSScriptRoot -Parent
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
