[CmdletBinding()]
param([switch]$DeepLearning, [switch]$Dev)

$ErrorActionPreference = "Stop"
$setupOptions = @()
if ($DeepLearning) { $setupOptions += '--deep-learning' }
if ($Dev) { $setupOptions += '--dev' }
python (Join-Path $PSScriptRoot "workbench.py") setup @setupOptions
exit $LASTEXITCODE
