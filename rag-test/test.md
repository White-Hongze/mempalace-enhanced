### A4
ROS2 订阅回调中禁止做哪些长阻塞操作？

答：
在 ROS2 订阅回调中，应禁止或避免以下长阻塞操作（尤其是高频回调路径）：
1. 线程阻塞等待：如 `sleep/sleep_for/usleep`、长时间 `wait/wait_for/wait_until`。
2. 同步等待外部结果：如在回调里同步等待 service/action/future 完成（例如 `future.get()` 一直阻塞）。
3. 阻塞式 I/O：文件 I/O、网络 I/O、串口 I/O、数据库阻塞访问等。
4. 长时间持锁或锁内执行耗时逻辑：包括锁内调用外部代码、复杂计算、I/O，导致回调堆积与调度抖动。
5. 其他不可控耗时操作：会显著占用 executor 线程、降低实时性和吞吐的重阻塞路径。

补充说明：根据召回内容，规范核心是“回调保持轻量、避免阻塞 executor”，并把耗时任务转移到异步工作线程/任务队列处理。

MCP 召回 Top 5（query: `3.2.1 回调中禁止长时间阻塞 sleep_for usleep future.get wait_for spin_until_future_complete join 文件IO 网络IO 锁等待`）
1. score=0.03125763125763126
	- point_id: abbf3741-ee50-5d41-a2db-bdd7437e4c21
	- sources: dense,sparse
	- collection: docs
	- type: abstract
	- uri: 面向 RAG 召回测试的编码规范文档/2 并发编程规范/2.3 条件变量与等待机制/2.3.2 超时等待要明确处理策略/.abstract.md
	- snippet: 使用 wait_for/wait_until 时必须明确超时处理策略，不能忽略超时分支。

2. score=0.01639344262295082
	- point_id: 78ee33cd-9fc5-58f9-8511-e3ca0b4815a7
	- sources: dense
	- collection: docs
	- type: abstract
	- uri: 面向 RAG 召回测试的编码规范文档/3 ROS2 编码规范/3.2 回调执行规范/3.2.1 回调中禁止长时间阻塞/.abstract.md
	- snippet: 回调中的长时间阻塞会严重影响 executor 调度效率；该条目直接对应题目。

3. score=0.01639344262295082
	- point_id: 3f4c531c-7a34-56a4-9a8e-4ba49b9f53b2
	- sources: sparse
	- collection: docs
	- type: abstract
	- uri: 面向 RAG 召回测试的编码规范文档/3 ROS2 编码规范/3.2 回调执行规范/3.2.3 日志必须节流/.abstract.md
	- snippet: 高频回调中无节制日志会造成性能问题，属于回调轻量化约束的一部分。

4. score=0.016129032258064516
	- point_id: 8b35186f-5c87-59f6-9261-55a43414fc9f
	- sources: dense
	- collection: docs
	- type: abstract
	- uri: 面向 RAG 召回测试的编码规范文档/2 并发编程规范/2.1 锁的使用原则/.abstract.md
	- snippet: 最小化临界区、避免锁内外部调用，可减少死锁与不可预测时延。

5. score=0.016129032258064516
	- point_id: 16306d3f-7e77-52bf-8ed0-7de03043700c
	- sources: sparse
	- collection: docs
	- type: abstract
	- uri: RAG 演进路线/9 是更适合需要全局理解的问题。/.abstract.md
	- snippet: 非目标噪声召回（与 ROS2 回调阻塞主题相关性较弱）。