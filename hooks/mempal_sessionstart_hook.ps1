$inputData = [Console]::In.ReadToEnd()
$scriptPath = Join-Path $PSScriptRoot 'mempal_sessionstart_hook.sh'

$bashPath = $null
# Prefer Git Bash; skip WSL bash (System32\bash.exe) as it cannot access Windows paths
$candidates = @(
    'C:\Program Files\Git\bin\bash.exe',
    'C:\Program Files (x86)\Git\bin\bash.exe'
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $bashPath = $c; break }
}
if (-not $bashPath) {
    $bashCmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($bashCmd -and $bashCmd.Source -notlike '*System32*') {
        $bashPath = $bashCmd.Source
    }
}

if (-not $bashPath) {
    '{"systemMessage":"SessionStart hook skipped: bash not found."}'
    exit 0
}

$inputData | & $bashPath $scriptPath
exit $LASTEXITCODE