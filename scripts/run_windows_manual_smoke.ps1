[CmdletBinding()]
param(
    [ValidateSet("offline", "manual-connect")]
    [string]$Mode = "offline",
    [string]$PythonExe = (Join-Path $PSScriptRoot "..\.venv312\Scripts\python.exe"),
    [switch]$ConfirmPersistentProfile
)

$ErrorActionPreference = "Stop"
$resolvedPython = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path

$versionText = & $resolvedPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить выбранный python.exe." }
$pythonVersion = [version]$versionText.Trim()
if ($pythonVersion -lt [version]"3.12") {
    throw "Требуется отдельный Python 3.12+. Установка или изменение существующего Python скриптом не выполняется."
}

& $resolvedPython -c 'import sys, struct, pathlib, PySide6, shiboken6; from PySide6.QtCore import qVersion; bits=struct.calcsize("P")*8; print(f"Python: {sys.version.split()[0]} ({bits}-bit)"); print(f"PySide6: {PySide6.__version__}; Qt: {qVersion()}"); print(f"shiboken6: {shiboken6.__version__} ({pathlib.Path(shiboken6.__file__).name})")'
if ($LASTEXITCODE -ne 0) { throw "Не удалось прочитать версии Python/Qt; приложение не запущено." }

if ($Mode -eq "manual-connect") {
    if (-not $ConfirmPersistentProfile) {
        throw "Режим manual-connect использует пользовательскую SQLite. Повторите с -ConfirmPersistentProfile после резервной копии."
    }
    $confirmation = Read-Host "Для разрешения запуска с пользовательским профилем введите USE-MY-LOCAL-DATA"
    if ($confirmation -cne "USE-MY-LOCAL-DATA") { throw "Подтверждение не принято; приложение не запущено." }
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
$appExitCode = 0
try {
    $appMode = "--$Mode"
    Write-Host "Запуск python.exe в текущем терминале. Режим: $Mode. Закройте окно приложения штатно."
    & $resolvedPython -X faulthandler -m chess_coach $appMode
    $appExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $appExitCode
