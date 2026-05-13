# MemPalace Hooks — SessionStart 自动归档

本目录目前只使用一个 hook：`SessionStart`。

每次新建聊天会话时，hook 会自动尝试将上一次会话的对话记录归档入 MemPalace。

## 文件说明

- `mempal_sessionstart_hook.sh`：主逻辑脚本（Linux/macOS 直接运行，Windows 通过 Git Bash 转发）
- `mempal_sessionstart_hook.ps1`：Windows 包装脚本，负责找到 Git Bash 并转发 stdin
- `mempalace.windows.json`：Windows 系统的 VS Code Copilot hook 配置模板
- `mempalace.linux.json`：Linux 系统的 VS Code Copilot hook 配置模板
- `mempalace.macos.json`：macOS 系统的 VS Code Copilot hook 配置模板

## 安装步骤

1. 按你的系统选对应的模板文件。
2. 将模板内容复制到你本机的 Copilot hook 配置文件（通常命名为 `mempalace.json`，放在 `~/.copilot/hooks/` 下）。
3. 模板里的路径已按约定写好：
   - Windows：`D:\dev\mempalace`
   - Linux/macOS：`~/dev/mempalace`
   - 如果你的实际安装位置不同，改一下 command 里的路径即可。

Linux/macOS 首次需要赋予执行权限：

```bash
chmod +x ~/dev/mempalace/hooks/mempal_sessionstart_hook.sh
```

## 工作原理

1. 从 stdin 读取当前 hook payload（包含 session id 和对话记录文件路径）。
2. 加载 `~/.mempalace/hook_state/last_session_meta`（上一次会话的元数据）。
3. 如果检测到与上次不同的会话且记录文件存在，则：
   - 把对话记录复制到临时暂存目录；
   - 执行 `mempalace mine <暂存目录> --mode convos` 进行归档。
4. 将本次会话元数据写回 `last_session_meta`。
5. 返回一条 JSON `systemMessage`，说明结果（`autosaved` / `skipped` / `failed`）。

## 运行依赖

- 需要 Python 可用（优先使用仓库内 `.venv`，其次回退到系统 `python3` / `python`）
- Windows 上需要 Git Bash（优先查找 `C:\Program Files\Git\bin\bash.exe`，会自动跳过 WSL 的 bash）

依赖缺失时，hook 会优雅退出并输出跳过提示，不会报错中断会话。

## 调试

查看 hook 运行日志：

```bash
cat ~/.mempalace/hook_state/hook.log
```

相关状态文件：

- `~/.mempalace/hook_state/last_session_meta`
- `~/.mempalace/hook_state/ingest_stage/`
