param([switch]$Release)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
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
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Rust 未安装，请先安装 Rust 工具链。' }
    $buildArgs = @('build', '--locked')
    if ($Release) { $buildArgs += '--release' }
    & cargo @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Rust 编译失败。' }
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
    Write-Host "构建完成：$projectRoot\GanganMouse.exe"
} finally { Pop-Location }
