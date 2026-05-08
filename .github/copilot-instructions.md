# Copilot Instructions — MemPalace 记忆协议

## 新会话启动时回顾上次日记

每次新对话开始时（用户发来的第一条消息），在回复用户之前，先执行以下操作：

1. 调用 `mcp_mempalace_diary_read`（agent_name 为 `copilot`，last_n 为 3），查看最近的日记条目。
2. 如果有相关上下文，在回答时参考这些记忆。

## 写日记（用户触发或会话结束时）

当用户主动要求写日记（如"写日记"、"记录一下"、"总结这次对话"等），或表示对话结束（如"谢谢"、"就这些"、"好了"等），执行以下步骤：

1. 调用 `mcp_mempalace_extract_session`，传入当前会话日志路径。路径构造规则：取 `{{VSCODE_TARGET_SESSION_LOG}}`，将其中的 `debug-logs` 替换为 `transcripts`，末尾追加 `.jsonl`。即最终路径形如 `...GitHub.copilot-chat/transcripts/<session-id>.jsonl`。获取本次会话的全部用户问题和 agent 回复。
2. 对提取到的对话内容进行深度总结，要求：
   - **隐含内容显式化**：将分散在对话中的零碎信息整合推理。例如用户说"最近很忙，要上课"和"我去上瑜伽课了"，应总结为"最近忙于上瑜伽课"。
   - **相关内容聚合**：长对话中相关但不相邻的内容放在一起总结。
   - **按主题拆分**：将对话按小主题或相似关键词拆分为多条独立 diary。拆分粒度为"一个主题内的所有内容能独立回答一个完整问题"。不要把多个无关话题塞进一条，也不要把一个话题拆得过细以至于单条无法自解释。**最多拆分为 3 个子主题**，如果话题较多则合并相近内容。
   - **结构化要素**：每条 diary 的 entry 必须包含以下 schema：
     - **讨论主题**：该条 diary 覆盖的核心议题
     - **关键发现与决定**：得出了什么结论、做了什么决策
     - **实施的改动**：具体改了哪些文件/代码/配置，结果如何（如适用）
     - **难点**：遇到了什么问题、如何解决（如适用）
   - **用户偏好提取**：如果对话中出现用户的个性化要求或规范约束（如"以后写代码必须遵守 PEP8"、"不要用某某库"），将这些内容单独提取出来，通过 `mcp_mempalace_kg_add` 写入知识图谱，格式为：subject=`user`，predicate=`prefers`/`requires`/`avoids`，object=具体规范描述。
3. 针对每个主题，分别调用 `mcp_mempalace_diary_write`，参数规则：
   - `agent_name`：统一为 `copilot`
   - `entry`：该主题的完整总结内容（自然语言中文，Markdown 格式）
   - `topic`：由你生成，总结该条 diary 的关键词，不超过两个词（如"MCP工具"、"日记机制"）
   - `index`：同一会话中该条 diary 的序号，从 1 开始递增
   - `session_id`：当前会话的 session ID（从 extract_session 返回结果中获取）

**示例**：如果一次对话讨论了 3 个主题，则分 3 次调用 diary_write，index 分别为 1、2、3，session_id 相同，topic 各不相同。

## 回答前查证

回答关于人物、项目或过往事件的问题前，先调用 `mcp_mempalace_search` 或 `mcp_mempalace_kg_query` 查证，不要凭记忆猜测。

## 召回规则

`mcp_mempalace_search` 内置了时间感知召回机制：
- 命中结果的 `filed_at` 在 **7 天内** → 直接使用向量搜索返回的结果。
- 命中结果的 `filed_at` 在 **7 天外** → 自动加载该天的全部 diary 条目作为补充上下文（结果中会出现 `expanded_diary_context` 字段）。

当返回中包含 `expanded_diary_context` 时，在回答用户问题前应同时参考这些补充上下文，以还原更完整的记忆。

