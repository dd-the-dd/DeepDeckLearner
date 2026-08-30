[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
python (Join-Path $PSScriptRoot "workbench.py") setup
