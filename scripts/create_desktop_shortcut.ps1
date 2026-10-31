$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $repoRoot ".venv\Scripts"
$pythonw = Join-Path $venv "pythonw.exe"
$python = Join-Path $venv "python.exe"

if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "Не найдено окружение .venv\Scripts. Создайте .venv и установите зависимости проекта по инструкции README.md, затем повторите запуск скрипта. Изменения системы и установка пакетов скриптом не выполняются."
    exit 1
}

$versionText = (& $python --version 2>&1 | Out-String).Trim()
if ($versionText -notmatch "Python (\d+)\.(\d+)") {
    Write-Error "Не удалось определить версию Python из '$versionText'. Ярлык не создан."
    exit 1
}
$major = [int]$Matches[1]
$minor = [int]$Matches[2]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
    Write-Warning "Найден $versionText, а проект требует Python 3.12+. Ярлык будет создан для текущей .venv, но эта версия не является поддерживаемой. Установите Python 3.12+, пересоздайте .venv по README.md и запустите скрипт повторно для обновления ярлыка."
}

$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders("Desktop")
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    Write-Error "Windows не сообщил доступную папку Рабочего стола. Ярлык не создан."
    exit 1
}
$shortcutPath = Join-Path $desktop "Chess Coach.lnk"
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -and
        [IO.Path]::GetFullPath($existing.TargetPath) -ne [IO.Path]::GetFullPath($pythonw)) {
        Write-Error "На Рабочем столе уже есть 'Chess Coach.lnk', ведущий в другое место. Он не перезаписан: $shortcutPath"
        exit 1
    }
}

$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m chess_coach"
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = "Chess Coach — локальная шахматная доска и Lichess-клиент"
$shortcut.IconLocation = "$pythonw,0"
$shortcut.Save()

Write-Host "Создан ярлык: $shortcutPath"
Write-Host "Запуск: pythonw.exe -m chess_coach; рабочая папка: $repoRoot"
