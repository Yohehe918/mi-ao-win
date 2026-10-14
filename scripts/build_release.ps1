param(
    [string]$OutputDirectory = '',
    [string]$Compiler = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist\release' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
Push-Location $projectRoot
try {
    python -B -X utf8 scripts\prepare_release.py
    if ($LASTEXITCODE) { throw 'Asset preparation failed' }
    python -B -X utf8 -m pytest -q -k 'not test_clipboard'
    if ($LASTEXITCODE) { throw 'Tests failed' }
    python -B -X utf8 -m PyInstaller --noconfirm --clean --distpath $OutputDirectory 米遥.spec
    if ($LASTEXITCODE) { throw 'Application build failed' }
    if (-not $Compiler) {
        $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
        if (Test-Path -LiteralPath $candidate) { $Compiler = $candidate }
    }
    if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) { throw 'Pass -Compiler with an Inno Setup 6 ISCC.exe path' }
    & $Compiler "/DReleaseDir=$OutputDirectory" installer\mi-ao-win.iss
    if ($LASTEXITCODE) { throw 'Installer build failed' }
    Copy-Item -LiteralPath docs\使用说明.txt,docs\PRIVACY.md,LICENSE,NOTICE,THIRD_PARTY_NOTICES.md -Destination $OutputDirectory -Force
    Copy-Item -LiteralPath assets\licenses -Destination $OutputDirectory -Recurse -Force
    python -B -X utf8 scripts\privacy_audit.py --source $projectRoot --artifacts $OutputDirectory
    if ($LASTEXITCODE) { throw 'Privacy audit failed' }
    $lines = Get-ChildItem -LiteralPath $OutputDirectory -Filter '*.exe' | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()+'  '+$_.Name }
    [IO.File]::WriteAllLines((Join-Path $OutputDirectory 'SHA256SUMS.txt'),$lines,[Text.UTF8Encoding]::new($false))
} finally { Pop-Location }
