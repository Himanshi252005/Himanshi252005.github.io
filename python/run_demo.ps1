$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command python -ErrorAction SilentlyContinue) {
    python -m orange_investigator investigate --incident INC-004
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 -m orange_investigator investigate --incident INC-004
} else {
    throw 'Install Python 3.11 or newer, then rerun this script.'
}
exit $LASTEXITCODE
