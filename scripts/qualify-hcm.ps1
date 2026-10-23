param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-qualification"
& $Python -m venv $venv
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$pythonExe = Join-Path $venv "Scripts/python.exe"
& $pythonExe -m pip install -r (Join-Path $root "constraints/ontohistoria-qualification.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonExe -m pip install -r (Join-Path $root "constraints/test-langchain.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonExe -m pip install pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location $root
try {
    & $pythonExe -m pytest -q qualification
    exit $LASTEXITCODE
} finally { Pop-Location }
