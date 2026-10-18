[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Virtual environment not found. Run: python -m venv .venv; .venv\Scripts\python.exe -m pip install -e '.[dev]'"
}

Set-Location $projectRoot
& $pythonExecutable -m uvicorn graphops.api:app --host 127.0.0.1 --port $Port
