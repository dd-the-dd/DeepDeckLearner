[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPath = Join-Path $projectRoot ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"

git -C $projectRoot submodule update --init --recursive

if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv $venvPath
}

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -e (Join-Path $projectRoot "external\deepdeck-agent")

$frontendPath = Join-Path $projectRoot "apps\learner-web"
npm --prefix $frontendPath ci
npm --prefix $frontendPath run build

& $pythonPath -m pip install -e "$($projectRoot)[deep-learning,dev]"

Write-Host "DeepDeckLearner is ready."
Write-Host "Launch: $venvPath\Scripts\deepdeck-learner.exe"
