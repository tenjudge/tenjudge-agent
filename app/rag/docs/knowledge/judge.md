# TenJudge Judge 模块实现说明

本文档记录 `tenjudge-judge` 的真实实现结构、关键流程、数据流和并发设计，目标是给后续 RAG 检索提供可直接回答问题的知识底座。文档以当前代码为准，不按理想设计写。

---

## 1. 模块定位

`tenjudge-judge` 是 TenJudge 在线评测系统的测评服务，负责接收提交消息、拉取题目测试数据、调用 Go-Judge 沙箱编译和运行代码、执行 checker、落库测评结果，并在结束后向消息系统发送完成通知。

当前仓库里实际包含四层职责：

1. **消息入口层**：从 RabbitMQ 消费提交 ID。
2. **Work 层**：按语言组织一次完整测评流程。
3. **Sandbox 层**：封装 Go-Judge 的编译、运行、检查、文件上传下载和删除。
4. **基础设施层**：数据库、MinIO、Redis、RabbitMQ、全局缓存、线程池配置。

---

## 2. 启动阶段做了什么

应用入口是 `TenjudgeJudgeApplication`，本身只负责启动 Spring Boot。真正的初始化工作由 `GoJudgeInitRunner` 完成，它在应用启动后先把 Go-Judge 运行所需的基础文件准备好。

### 2.1 启动初始化顺序

`GoJudgeInitRunner` 的行为是：

1. 从 classpath 读取 `src/testlib.h`，上传到 Go-Judge，保存为 `GlobalData.testlibFileId`。
2. 并行编译 `src/summarizer.cpp`，保存为 `GlobalData.summarizerFileId`。
3. 并行编译 `src/fcmp.cpp`、`src/lcmp.cpp`、`src/wcmp.cpp`，分别保存到 `GlobalData.fcmpFileId`、`lcmpFileId`、`wcmpFileId`。
4. 等待所有并行任务结束后，再允许应用继续对外提供服务。

### 2.2 这些基础文件的作用

- `testlib.h`：给 special judge 编译器使用。
- `summarizer.cpp`：用于把输入、输出、答案文件转换成可展示的摘要。
- `fcmp/lcmp/wcmp`：普通题目直接复用的内置 checker。

这几个文件的 fileId 会放入 `GlobalData`，后续所有测评流程都直接复用，不会每次重复编译。

---

## 3. 消息入口与结束通知

### 3.1 消息消费

`Listener.receiveMessage(Long submissionId)` 监听队列 `tenjudge.judge.submit.queue`。

处理流程是：

1. 根据 `submissionId` 从数据库读取 `Submission`。
2. 根据 `submission.language` 选择 `WorkService`。
3. 目前仅支持 `type == "judge"` 的提交。
4. 调用对应语言的 `judge(submission)` 执行完整测评。

如果任一步失败：

- 记录日志。
- 尝试把该提交状态更新成 `SYSTEM_ERROR`。

无论成功失败，`finally` 都会调用 `Producer.send(submissionId)`，向交换机 `tenjudge.judge.exchange` 发送 routing key 为 `complete` 的消息，表示这份提交已经处理完毕。

### 3.2 RabbitMQ 配置

`RabbitConfig` 声明了：

- exchange：`tenjudge.judge.exchange`
- queue：`tenjudge.judge.submit.queue`
- binding key：`submit`

`application.yaml` 里把 listener 的 `prefetch` 设置为 1，说明每个消费者一次只预取一条消息，避免单个消费者堆积过多待处理任务。

---

## 4. Work 层总体结构

`WorkService` 是统一接口，目前有两个实现：

- `CppWorkService`
- `PythonWorkService`

`WorkServiceFactory` 根据语言字符串选择实现，当前只映射：

- `cpp`
- `python`

### 4.1 统一的测评骨架

两个实现的总体骨架一致：

1. 从数据库读取题目信息。
2. 调用 `UpdateService.updateProblem(problem)`，确保本地和 Go-Judge 中的题目缓存是最新版本。
3. 从 `GlobalData.problemCache` 取出该题目的缓存 fileId。
4. 从 MinIO 读取提交源码。
5. 组装 `JudgeRequest`。
6. 调用语言对应的 judge service。
7. 把测评总结果写回 `submission`。
8. 把每个测试点的结果批量写入 `submission_detail`。

### 4.2 C++ 与 Python 的差异

- `CppWorkService` 走 `CppJudgeService`。
- `PythonWorkService` 走 `PythonJudgeService`。
- 两者都共享同一套 checker 和测试数据缓存机制。
- Python 判题没有单独的编译阶段，直接把源码交给 `PythonDriver.run()`。

---

## 5. 题目缓存更新机制

题目缓存更新由 `UpdateService` 负责，这是整个系统里最关键的基础设施之一。

### 5.1 缓存对象

`GlobalData.problemCache` 保存的是 `ProblemCacheEntry`，字段只有四个：

- `problemKey`：版本标识
- `checkerFileId`：checker 可执行文件的 Go-Judge fileId
- `inputFileIds`：所有测试点输入文件的 fileId 列表
- `answerFileIds`：所有测试点答案文件的 fileId 列表

### 5.2 更新入口

`updateProblem(problem)` 的目标是把某个题目的缓存刷新到最新状态，并确保 checker 已经可执行。

### 5.3 锁设计

更新过程使用两层锁：

1. **本地锁**：`GlobalData.getUpdateLock(problemId)` 返回一个按题目 ID 复用的 `ReentrantLock`，防止单进程内同题并发更新。
2. **分布式锁**：`RedissonClient.getReadWriteLock("lock:problem:" + problemId)` 获取题目锁。当前代码实际使用的是 `readLock()`。

锁的顺序是先本地锁，再分布式锁。这样可以先拦住 JVM 内部并发，再协调多实例之间的更新竞争。

### 5.4 更新步骤

实际更新顺序是：

1. 检查题目 `problemKey` 是否存在。
2. 读取当前缓存 `GlobalData.problemCache.get(problemId)`。
3. 如果缓存中的 `problemKey` 与数据库一致，并且 `checkerFileId` 已存在，直接返回成功。
4. 创建新的 `ProblemCacheEntry`，先完整构建新缓存。
5. 拉取题目的 `input/*.in` 和 `answer/*.ans`，并逐个上传到 Go-Judge，得到 fileId 列表。
6. 根据 `problem.checker` 决定 checker：
   - `fcmp`：使用启动阶段预编译好的 fileId
   - `lcmp`：使用启动阶段预编译好的 fileId
   - `wcmp`：使用启动阶段预编译好的 fileId
   - `special`：从 MinIO 读取 `checker.cpp`，再用 `CppDriver.compileChecker()` 编译
7. 全部成功后，原子性地把新缓存写入 `GlobalData.problemCache`。
8. 异步延迟删除旧 fileId，避免正在运行的提交误用旧缓存时文件已经被删掉。

### 5.5 旧缓存删除策略

`deleteOldFilesAsyncWithDelay()` 的原则是“谁创建谁删除”，但要保留全局共享基础文件和新缓存文件。

保留集合包括：

- 新缓存的 checker、input、answer fileId
- `summarizerFileId`
- `testlibFileId`
- `fcmpFileId`
- `lcmpFileId`
- `wcmpFileId`

旧文件删除分两步：

1. 先延迟等待一段时间。
2. 再用异步并发方式逐个删除。

`application.yaml` 里默认 `cache-delete-delay` 是 10 秒，代码里的默认值是 60 秒，实际运行取配置项优先。

---

## 6. Go-Judge 沙箱调用方式

沙箱相关封装在 `sandbox.driver` 下，核心有三个类：

- `CommonDriver`
- `CppDriver`
- `PythonDriver`

### 6.1 CommonDriver

`CommonDriver` 负责所有语言共享的操作：

1. `uploadFile(InputStream, fileName)`：上传文件到 Go-Judge 的文件存储，返回 fileId。
2. `downloadFile(fileId)`：从 Go-Judge 下载文件内容。
3. `deleteFile(fileId)`：删除 Go-Judge 中文件。
4. `asyncDeleteFile(fileId)`：异步删除，删除失败只记日志。
5. `summarize(fileId)`：运行 `summarizer` 可执行文件，对输入/输出/答案做文本摘要。

`summarize()` 会把文件作为 prepared file 传入 Go-Judge，再读取 stdout 作为摘要结果。若沙箱没有正常返回，方法不会抛出业务异常，而是返回空字符串，避免把展示辅助功能升级成系统错误。

### 6.2 CppDriver

`CppDriver` 提供四个动作：

1. `compile(code)`：编译选手 C++ 源码。
2. `compileChecker(code)`：编译 testlib 风格 checker。
3. `run(executableFileId, inputFileId, timeLimit, memoryLimit)`：运行选手程序。
4. `check(checkerFileId, inputFileId, outputFileId, answerFileId)`：运行 checker 比对输出。

### 6.3 PythonDriver

`PythonDriver` 只负责运行 Python 源码：

- 运行命令是 `python3 main.py`
- 直接把源码写成 `main.py` 传给 Go-Judge
- 运行结果会映射为 `SUCCESS / TIME_LIMIT_EXCEEDED / MEMORY_LIMIT_EXCEEDED / RUNTIME_ERROR`

### 6.4 Go-Judge 请求模型

`GoJudgeCmd` 里最重要的字段是：

- `args`：要执行的命令
- `env`：环境变量
- `files`：stdin/stdout/stderr 等文件配置
- `cpuLimit`、`clockLimit`、`memoryLimit`、`procLimit`
- `copyIn`：执行前复制进容器的文件
- `copyOut`：直接取回文件内容
- `copyOutCached`：返回缓存 fileId

`GoJudgeRequest` 的顶层字段名是 `Cmd`，这是按 Go-Judge API 的字段名来的。

---

## 7. C++ 判题流程

`CppJudgeService.judge(request)` 是当前最完整的判题实现。

### 7.1 输入参数

`JudgeRequest` 包含：

- `submissionId`
- `code`
- `checkerFileId`
- `timeLimit`
- `memoryLimit`
- `inputFileIds`
- `answerFileIds`

输入和答案 fileId 必须等长，否则直接抛异常。

### 7.2 第一步：编译

先调用 `CppDriver.compile(code)`。

编译命令固定为：

```bash
g++ -O2 -std=gnu++23 main.cpp -o main
```

如果编译失败：

- 直接返回 `COMPILE_ERROR`
- 不再进行任何测试点运行
- `submission_detail` 不会生成

### 7.3 第二步：固定大小异步滑动窗口

判题不是把所有测试点一次性全部扔进线程池，而是使用固定大小滑动窗口。

相关变量：

- `judgeBatchSize`：窗口大小，默认 3
- `AtomicBoolean isStopped`：是否已经停止继续分发新任务
- `AtomicInteger dispatchedCount`：已经分发了多少测试点
- `AtomicInteger runningCount`：当前还在跑的测试点数
- `CompletableFuture<Void> allDone`：所有已分发任务都结束后才会完成
- `RunAndJudgeResult[] allResults`：按测试点编号保存中间结果
- `lock`：同一个提交内的状态锁

#### 调度方式

1. 先启动最多 `judgeBatchSize` 个测试点。
2. 每个测试点完成后，如果当前没有被终止，就递归调度下一个测试点。
3. 如果某个测试点返回非 `ACCEPTED`，就设置 `isStopped = true`，停止继续分发。
4. 但是已经分发出去的测试点仍然会等它们执行完，之后再统一收口。

这个设计的目的很直接：

- 保证单个提交最多同时有固定数量测试点运行。
- 遇到 WA/RE/TLE/MLE 后尽快停止浪费资源。
- 仍然保留已经开始的测试点结果，方便展示连续测试点列表。

### 7.4 单个测试点的执行链

`runAndJudge(request)` 的处理链是：

1. `cppDriver.run()` 运行选手程序。
2. 并行生成输入、输出、答案三个摘要。
3. 如果运行结果不是 `SUCCESS`，直接返回对应错误码，并删除 stdout/stderr 缓存。
4. 如果运行成功，再调用 `cppDriver.check()` 跑 checker。
5. 删除 stdout/stderr 缓存。
6. 返回当前测试点结果。

其中 `info` 字段在运行失败时为空，在 checker 失败时保存 checker 输出。

### 7.5 结果汇总

所有已分发任务完成后，`judge()` 会统一做后处理：

1. 等待 `allDone.join()`。
2. 删除编译出来的可执行文件。
3. 遍历所有测试点：
   - 没跑到的点填成 `SKIPPED`
   - 统计最大时间和最大内存
   - 记录第一个 `SYSTEM_ERROR`
   - 记录第一个非 `ACCEPTED`
4. 组装 `SubmissionDetail` 列表。
5. 生成最终总状态：
   - 优先 `SYSTEM_ERROR`
   - 否则优先第一个非 `ACCEPTED`
   - 否则 `ACCEPTED`

### 7.6 最终状态和测试点状态的关系

单个测试点状态可能是：

- `ACCEPTED`
- `WRONG_ANSWER`
- `RUNTIME_ERROR`
- `TIME_LIMIT_EXCEEDED`
- `MEMORY_LIMIT_EXCEEDED`
- `SYSTEM_ERROR`
- `SKIPPED`

提交总状态目前会优先选择：

1. `SYSTEM_ERROR`
2. 第一个非 `ACCEPTED`
3. `ACCEPTED`

也就是说，如果中间有系统错误，即使前面也有 WA/TLE，最终提交状态仍会被系统错误覆盖。

---

## 8. Python 判题流程

`PythonJudgeService` 的整体结构和 C++ 一致，差异只在运行阶段。

### 8.1 运行方式

Python 判题不编译，直接调用 `PythonDriver.run(code, inputFileId, timeLimit, memoryLimit)`。

### 8.2 结果映射

Python 运行结果映射规则是：

- `Accepted` -> `Code.SUCCESS`
- `Time Limit Exceeded` -> `Code.TIME_LIMIT_EXCEEDED`
- `Memory Limit Exceeded` -> `Code.MEMORY_LIMIT_EXCEEDED`
- `Nonzero Exit Status` -> `Code.RUNTIME_ERROR`
- 其他异常态 -> `Code.RUNTIME_ERROR`

### 8.3 checker 复用

Python 的输出比对仍然复用 `CppDriver.check()`，也就是同一套 testlib checker。

---

## 9. 数据库存储

### 9.1 submission 表

`Submission` 对应 `submission` 表，主要字段有：

- `id`
- `type`
- `problemId`
- `submitterId`
- `isAgent`
- `submitTime`
- `contestId`
- `language`
- `status`
- `timeUsedMs`
- `memoryUsedMb`
- `info`

其中：

- `status` 存的是最终测评状态字符串。
- `timeUsedMs` 和 `memoryUsedMb` 存整份提交的最大值。
- `info` 通常用于存编译错误或最终测评附加信息。

### 9.2 submission_detail 表

`SubmissionDetail` 对应 `submission_detail` 表，每个测试点一条记录：

- `submissionId`
- `testCaseId`
- `input`
- `output`
- `answer`
- `info`
- `status`
- `timeUsedMs`
- `memoryUsedMb`

这里保存的是展示层需要的信息：

- 输入摘要
- 输出摘要
- 答案摘要
- checker 或运行信息
- 当前测试点状态
- 当前测试点时间/内存

### 9.3 持久化方式

- `SubmissionUpdateService.update(submission)`：更新整条提交记录。
- `SubmissionUpdateService.updateStatus(submissionId, status)`：只更新状态。
- `SubmissionDetailUpdateService.batchInsert(details)`：逐条插入测试点详情。

当前 `batchInsert()` 实现是循环单条插入，不是 JDBC 批量写入。

---

## 10. 对象存储结构

系统使用 MinIO 存题面、测试数据、checker 源码等对象。

### 10.1 题目对象结构

```text
problem/<problem_key>/
    input/
        1.in
        2.in
    answer/
        1.ans
        2.ans
    checker.cpp   # 仅 special judge 时存在
```

### 10.2 提交对象结构

```text
submission/<submission_id>/
    code.(cpp/py/java)
```

### 10.3 MinIO 服务能力

`MinioService` 提供：

- 上传
- 下载
- 读取纯文本
- 删除
- 按前缀删除
- 生成预签名 URL

上传时如果桶不存在，会自动创建 `minio.bucket-name` 对应的 bucket。

---

## 11. Go-Judge 里的资源与临时文件

### 11.1 编译结果

`CppDriver.compile()` 和 `compileChecker()` 会把编译产物以 `copyOutCached` 的方式取回，得到一个可执行文件 fileId。

这个 fileId 后续会被当作 `PreparedFile` 再传入 Go-Judge，不需要再次上传源码。

### 11.2 运行结果

运行时会把 stdout/stderr 以 `copyOutCached` 的方式保存为临时 fileId，然后在本地通过 `CommonDriver.asyncDeleteFile()` 删除。

### 11.3 摘要结果

输入、输出、答案摘要由 `summarizer` 生成，只作为展示用途，不参与判题。

---

## 12. 线程池与并发配置

`AsyncConfig` 定义了三个线程池：

1. `judgeExecutor`
2. `summarizeExecutor`
3. `deleteExecutor`

当前代码里实际使用情况是：

- `judgeExecutor`：运行编译、运行、摘要等主要异步任务。
- `deleteExecutor`：异步删除 Go-Judge 缓存文件。
- `summarizeExecutor`：已定义，但当前摘要流程实际还是用的 `judgeExecutor`。

### 12.1 实际并发原则

系统尽量把“耗时但可并行”的操作拆开：

- 多测试点并行
- 输入/输出/答案摘要并行
- 旧文件并发删除
- 初始 checker 编译并行

但“单个提交的分发逻辑”仍然通过局部锁保证状态一致。

---

## 13. 题目和 checker 的状态判断

### 13.1 内置 checker

内置 checker 有：

- `fcmp`：逐字节比较
- `lcmp`：忽略行末空格
- `wcmp`：忽略所有空白符

### 13.2 special judge

如果 `problem.checker == "special"`：

1. 从 MinIO 读取 `checker.cpp`
2. 复制 `testlib.h`
3. 用 `CppDriver.compileChecker()` 编译
4. 编译产物 fileId 写入缓存

`check()` 阶段对 testlib 的退出码有约定：

- `0` -> `ACCEPTED`
- `1` 或 `2` -> `WRONG_ANSWER`
- 其他退出码或异常 -> 系统错误

---

## 14. 示例题与测试资源

测试资源位于 `src/test/resources/problem/P1/`，包含：

- `input/1.in` 到 `6.in`
- `answer/1.ans` 到 `6.ans`
- `code/ac.cpp`
- `code/wa.cpp`
- `code/tle.cpp`
- `code/re.cpp`
- `code/ce.cpp`

它们用于验证：

- 正确代码 -> `ACCEPTED`
- 逆序输出 -> `WRONG_ANSWER`
- 死循环 -> `TIME_LIMIT_EXCEEDED`
- 越界访问 -> `RUNTIME_ERROR`
- 语法错误 -> `COMPILE_ERROR`

这组样例基本覆盖了当前测评链路的主要分支。

---

## 15. 当前实现里需要知道的事实

1. `Listener` 只处理 `judge` 类型提交，`hack`、`run`、`check` 还没有完整接入。
2. `JudgeFinishException` 已存在，但当前主流程里没有作为统一控制流使用。
3. `PythonWorkService` 和 `CppWorkService` 的整体结构相同，Python 只是把“编译”替换成“直接运行源码”。
4. `UpdateService` 使用题目 `problemKey` 作为缓存版本识别依据。
5. 旧缓存删除是延迟异步执行，不是立刻删除。
6. `CommonDriver.summarize()` 失败时会返回空串，不会直接让整份提交失败。
7. `SubmissionDetailUpdateService` 目前是逐条插入，适合低复杂度实现，但不是最优批量写入方式。

---

## 16. 一条提交的完整生命周期

可以把整条链路概括成下面这 8 步：

1. 业务系统把 `submissionId` 投递到 RabbitMQ。
2. `Listener` 消费消息，查出 `Submission`。
3. `WorkServiceFactory` 按语言选中具体 WorkService。
4. `UpdateService` 确认题目测试数据和 checker 的缓存是最新版本。
5. WorkService 从 MinIO 读取源码，组装 `JudgeRequest`。
6. `CppJudgeService` 或 `PythonJudgeService` 执行编译/运行/check 的异步滑动窗口。
7. 汇总总状态和每个测试点详情，写入数据库。
8. `Producer` 发送完成消息，通知后续系统这个提交已经处理结束。

---

## 17. 适合 RAG 直接命中的问答点

下面这些是最常被问到的问题，对应的答案已经在代码里明确存在：

- **题目缓存怎么更新？**
  - 先锁，再比版本，再拉取 MinIO 测试点，再编译 checker，再整体替换缓存，最后延迟删除旧 fileId。

- **为什么不会一次性跑完全部测试点？**
  - 因为采用固定大小异步滑动窗口，只维持 `judgeBatchSize` 个并发测试点。

- **WA 出现后后面还会继续跑吗？**
  - 不会继续分发新的测试点，但已经分发出去的会跑完再统一收口。

- **测试点详情存什么？**
  - 输入摘要、输出摘要、答案摘要、info、状态、时间、内存。

- **普通题和 special judge 怎么统一的？**
  - 都走 `CppDriver.check()`，普通题只是 checker 用内置的 `fcmp/lcmp/wcmp`。

- **提交最终状态怎么定？**
  - 优先系统错误，其次第一个非 AC，否则 AC。

---

## 18. 结论

`tenjudge-judge` 的核心不是“单次运行一个程序”，而是“在消息驱动下，围绕题目缓存、Go-Judge 文件系统、checker 编译、并发测试点调度和结果落库，串起一条完整测评链路”。

这份实现的关键设计点是：

- 基础文件在启动时一次性准备
- 题目缓存按版本原子替换
- 测评采用固定窗口并发
- 运行和 checker 直接在 Go-Judge 中完成
- 结果按提交和测试点两层持久化

后续如果继续扩展 `hack / run / check` 类型，这份文档里的入口、缓存、沙箱、结果结构可以直接作为继续设计的基线。
