$inputData = [Console]::In.ReadToEnd()
$bashPath = 'C:/Program Files/Git/bin/bash.exe'
$scriptPath = Join-Path $PSScriptRoot 'mempal_sessionstart_hook.sh'

$inputData | & $bashPath $scriptPath
exit $LASTEXITCODE