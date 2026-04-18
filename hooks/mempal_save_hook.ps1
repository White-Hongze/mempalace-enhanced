$inputData = [Console]::In.ReadToEnd()
$bashPath = 'C:/Program Files/Git/bin/bash.exe'
$scriptPath = Join-Path $PSScriptRoot 'mempal_save_hook.sh'

$inputData | & $bashPath $scriptPath
exit $LASTEXITCODE