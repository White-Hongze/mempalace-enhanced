#!/bin/bash
set -euo pipefail

/usr/bin/python3 - <<'PYEOF'
import json, os, shutil, datetime, sys
from collections import OrderedDict

stable   = os.path.expanduser('~/.config/Code/User/settings.json')
insiders = os.path.expanduser('~/.config/Code - Insiders/User/settings.json')

if os.path.exists(stable):
    settings_path = stable
elif os.path.exists(insiders):
    settings_path = insiders
else:
    settings_path = stable

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
mcp_file_path = os.path.join(os.path.dirname(settings_path), 'mcp.json')

if not os.path.exists(settings_path):
    with open(settings_path, 'w', encoding='utf-8') as f:
        f.write('{}')

with open(settings_path, 'r', encoding='utf-8') as f:
    raw = f.read().strip() or '{}'

try:
    settings = json.loads(raw)
except json.JSONDecodeError as e:
    print('[ERROR] settings.json is not valid JSON:', e)
    print('[ERROR] Please remove comments from settings.json and retry.')
    sys.exit(1)

target_mcp = OrderedDict([
    ('servers', OrderedDict([
        ('97c39a7ad2384fbdb063a9dcc40ee6ea', OrderedDict([
            ('url', 'http://8.147.57.160:15000/mcp'),
            ('type', 'http')
        ]))
    ])),
    ('inputs', [])
])

file_mcp = None
if os.path.exists(mcp_file_path):
    try:
        with open(mcp_file_path, 'r', encoding='utf-8') as f:
            file_mcp = json.loads(f.read().strip() or '{}')
    except json.JSONDecodeError:
        file_mcp = None

if settings.get('mcp') == target_mcp and file_mcp == target_mcp:
    print('[OK] MCP config already up to date. No changes made.')
    sys.exit(0)

ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup = settings_path + '.bak.' + ts
shutil.copy2(settings_path, backup)

settings['mcp'] = target_mcp
with open(settings_path, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)

with open(mcp_file_path, 'w', encoding='utf-8') as f:
    json.dump(target_mcp, f, indent=2, ensure_ascii=False)

print('[OK] MCP config written to VS Code settings.')
print('[OK] MCP file written:', mcp_file_path)
print('[OK] Backup created:', backup)
PYEOF

echo
echo '[OK] Finished. Restart VS Code to apply the MCP configuration.'
read -r -p 'Press Enter to continue...'
