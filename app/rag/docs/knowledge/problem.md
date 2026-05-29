# Problem 题目模块

`problem` 模块负责题目的导入、更新、查询、权限控制、测试数据存储和题目缓存。它是 TenJudge 服务端里最依赖“数据库 + MinIO + Redis + Redisson + 比赛上下文”的模块之一，很多看起来只是“查题”的接口，实际都会穿过一条完整的一致性链路。

本文档按“对外接口 -> 文件格式 -> 数据模型 -> 业务链路 -> 缓存与权限 -> 内部类职责”的顺序整理。

相关代码入口：

- [ProblemController](../problem/controller/ProblemController.java)
- [ProblemService](../problem/service/ProblemService.java)
- [ProblemCacheService](../problem/service/ProblemCacheService.java)
- [ProblemPermissionChecker](../problem/service/ProblemPermissionChecker.java)
- [ProblemRequestChecker](../problem/service/ProblemRequestChecker.java)
- [FileService](../problem/storage/FileService.java)
- [ProblemQueryService](../problem/persistence/ProblemQueryService.java)
- [ProblemUpdateService](../problem/persistence/ProblemUpdateService.java)
- [ProblemTagQueryService](../problem/persistence/ProblemTagQueryService.java)
- [ProblemTagUpdateService](../problem/persistence/ProblemTagUpdateService.java)

## 1. 模块职责与边界

这个模块的职责不是“简单增删改查题目记录”，而是同时维护以下几层一致性：

1. PostgreSQL 中 `problem` / `problem_tag` 的题目元数据。
2. MinIO 中题目测试点、checker 和判题相关文件。
3. Redis 中题目详情缓存、标签缓存、公开题目分页缓存。
4. Redisson 分布式读写锁，防止题目更新和题目查询并发读写冲突。
5. 与 `contest` 模块的联动缓存失效，避免题目标题变更后比赛详情长期显示旧值。

题目相关的“文件校验、内容校验、权限校验、缓存失效、对象存储切换”都放在 `service` 层完成，`controller` 只负责接收请求并返回 `Result<T>`.

## 2. 对外接口

`ProblemController` 当前提供以下接口：

| 方法 | 路径 | 作用 | 是否要求登录 |
| --- | --- | --- | --- |
| `GET` | `/problem` | 分页查询公开题目列表 | 否 |
| `GET` | `/admin/problem` | 管理员分页查询全部题目 | 是，管理员 |
| `GET` | `/admin/problem/mine` | 管理员分页查询自己创建的题目 | 是，管理员 |
| `POST` | `/problem` | 通过 zip 文件创建题目 | 是，管理员 |
| `PUT` | `/problem` | 通过 zip 文件更新题目 | 是，管理员 |
| `PATCH` | `/problem/visibility` | 修改题目可见性 | 是，超级管理员 |
| `GET` | `/problem/{id}` | 通过题目 ID 查询题目 | 否，公开题可匿名查看 |
| `GET` | `/agent/problem/{id}` | Agent 通过题目 ID 查询题目 | 否，但权限比普通请求更严格 |
| `GET` | `/contest/{contestId}/problem/{index}` | 在比赛上下文中按题号查询题目 | 否，受比赛状态约束 |

### 2.1 返回对象

这个模块常见的 VO 如下：

- `ProblemVO`：题目详情。
- `ProblemPageVO`：公开题目分页列表。
- `AdminProblemPageVO`：管理员题目分页列表。
- `CreateProblemVO`：创建成功后的题目 ID 和名称。

### 2.2 接口风格

创建和更新接口都使用 `multipart/form-data`，通过 `@ModelAttribute` 绑定请求对象：

- `ProblemCreateRequest` 只包含 `zipFile`。
- `ProblemUpdateRequest` 包含 `id` 和 `zipFile`。

这样做的原因是 OpenAPI 可以正确展示文件字段，而不是把文件参数散落在 Controller 方法签名里。

## 3. 题目文件格式

题目不是逐字段提交 JSON，而是通过一个 zip 包整体导入。当前实现要求 zip 里直接放文件，不允许再套一层额外目录。

### 3.1 目录结构

导入包内的结构约定如下：

```text
config.yaml
statement.md
solution.md
checker.cpp
/input
    1.in
    2.in
/answer
    1.ans
    2.ans
```

其中：

- `solution.md` 可选。
- `checker.cpp` 仅在 `checker = special` 时必需。
- `input` 与 `answer` 必须从 `1` 开始连续成对存在。

### 3.2 配置文件格式

`config.yaml` 会被解析为 [ProblemConfig](../problem/dto/ProblemConfig.java)，字段为：

- `name`
- `time_limit`
- `memory_limit`
- `checker`
- `difficulty`
- `tags`

当前代码使用 SnakeYAML 解析，因此配置字段名就是下划线风格，不是 Java 常见的驼峰命名。

示例：

```yaml
name: "Two Sum Problem"
time_limit: 1500
memory_limit: 256
checker: "special"
difficulty: 1600
tags:
  - "sortings"
  - "hashing"
```

### 3.3 当前支持的 checker

`checker` 的合法值由 [Checker](../common/Checker.java) 枚举定义，当前支持：

- `fcmp`
- `lcmp`
- `wcmp`
- `special`

当 `checker = special` 时，zip 中必须包含 `checker.cpp`。

### 3.4 当前支持的标签

题目标签由 [Tag](../common/Tag.java) 枚举约束，只有枚举内定义的字符串才允许写入 `problem_tag` 表。`ProblemRequestChecker` 会逐个校验 tags。

### 3.5 文件完整性规则

当前导入校验的核心规则是：

1. `config.yaml` 必须存在。
2. `statement.md` 必须存在。
3. `checker = special` 时，`checker.cpp` 必须存在。
4. `input/1.in` 和 `answer/1.ans` 必须同时存在。
5. `input/i.in` 与 `answer/i.ans` 必须从 `1` 开始连续成对出现。
6. 任意下标只存在一侧文件时，整份请求判定为非法。

这里的“连续”口径是：从 `1` 开始向后扫描，遇到第一组缺失的成对文件就停止，前面的最大连续编号就是 `testCaseNum`。如果某个下标只存在 `in` 或只存在 `ans`，直接报错。

### 3.6 当前限制

目前题面 `statement.md` 不支持图片元素。  
目前配置文件只接受 `yaml` 后缀，不接受 `yml`。  
当前实现要求 zip 内不要再嵌套顶层目录，否则导入后的路径会和业务约定不一致。

## 4. 数据模型

### 4.1 PostgreSQL

#### `problem`

`problem` 表是题目的主表，字段如下：

- `id`：题目 ID，自增主键。
- `author_id`：作者 ID。
- `visibility`：可见性，`public` 或 `private`。
- `checker`：评测类型。
- `time_limit`：时间限制，毫秒。
- `memory_limit`：内存限制，MB。
- `name`：题目名称。
- `statement`：题面。
- `solution`：题解，可为空。
- `difficulty`：难度分值，可为空。
- `problem_key`：MinIO 对象目录指针，uuid。
- `version`：版本号，每次更新递增。
- `test_case_num`：测试点数量。

索引：

- `idx_problem_visibility_id`：按 `visibility` 过滤并按 `id ASC` 分页查询公开题。
- `idx_problem_author_id_id`：按作者分页查询题目。

#### `problem_tag`

题目标签关系表：

- `problem_id`
- `tag`

主键是 `problem_id + tag`，另有 `tag` 索引用于按标签检索。

### 4.2 MinIO

题目测试数据和判题相关文件统一存到 MinIO，路径结构是：

```text
problem/<problem_key>/
    input/
        1.in
        2.in
    answer/
        1.ans
        2.ans
    checker.cpp
```

其中 `problem_key` 对应数据库里的对象指针。更新题目时，本质上是“切换这个指针到新的 uuid 目录”。

### 4.3 Redis

题目模块使用的 Redis key 主要有以下几类：

- `lock:problem:{problemId}`：题目读写锁。
- `problem:{problemId}`：题目元数据缓存，值为 `Problem`。
- `problem_tags:{problemId}`：题目标签缓存，值为 `List<String>`。
- `problem_page:current:{current}:size:{size}`：公开题目分页列表缓存，值为 `ProblemPageVO`。
- `contest_problem:contest:{contestId}`：比赛题目编排缓存，值为 `ContestProblemDTO` 列表。

### 4.4 VO 字段边界

#### `ProblemVO`

完整题目详情返回：

- `id`
- `authorId`
- `visibility`
- `checker`
- `timeLimit`
- `memoryLimit`
- `name`
- `statement`
- `solution`
- `difficulty`
- `version`
- `tags`

#### 受限题面

在比赛中的 private 题，非管理员只返回：

- `id`
- `checker`
- `timeLimit`
- `memoryLimit`
- `name`
- `statement`

不会返回 `authorId`、`visibility`、`solution`、`difficulty`、`version`、`tags`。

#### `ProblemPageVO`

公开题目分页只返回摘要项：

- `id`
- `name`
- `difficulty`

#### `AdminProblemPageVO`

管理员分页只返回：

- `id`
- `name`
- `visibility`

#### `CreateProblemVO`

创建成功只返回：

- `id`
- `name`

## 5. 导入链路

创建题目的入口是 `POST /problem`，对应 [ProblemService.create()](../problem/service/ProblemService.java)。

### 5.1 总体流程

创建链路的顺序是：

1. 校验管理员权限。
2. 生成临时目录 uuid 和 `problem_key` uuid。
3. 将 zip 解压到 `app.file-storage.temp/problem/<uuid>/`。
4. 校验 zip 内容、配置内容和测试点连续性。
5. 构造 `Problem` 实体并写入 `problem` 表。
6. 写入 `problem_tag` 表。
7. 上传 checker、input、answer 到 MinIO 的新对象目录。
8. 删除临时目录。
9. 返回创建后的题目 ID 和名称。

### 5.2 具体实现细节

#### 权限

创建题目必须是管理员，调用 `authService.checkAdmin()`。

#### 题目可见性

当前实现中，创建时 `visibility` 固定写成 `private`。也就是说，题目先以私密状态进入系统，之后如果需要公开，需要走单独的可见性修改接口。

#### 作者信息

创建时 `authorId` 写成当前登录用户 ID。

#### 版本号

创建时 `version` 固定写成 `1`。

#### 测试点数量

`testCaseNum` 通过连续扫描 `input/i.in` 和 `answer/i.ans` 得到，统计的是最大连续成对存在的下标。

#### 题解

如果 `solution.md` 存在，会读入并写进 `problem.solution`；否则保持为空。

#### MinIO 对象前缀

创建成功后，文件会上传到：

```text
problem/<problem_key>/
```

#### 创建失败时的行为

临时目录会在 `finally` 中删除。  
数据库写入失败会触发事务回滚。  
但创建链路对 MinIO 的补偿删除并不如更新链路完整，因此如果上传过程中已经写入部分新对象，当前实现没有专门做前缀级回滚清理。

这是当前实现状态，后续如果要进一步收紧一致性，应该补一层 MinIO 补偿删除。

## 6. 更新链路

更新题目的入口是 `PUT /problem`，对应 [ProblemService.update()](../problem/service/ProblemService.java)。

### 6.1 总体流程

更新链路的顺序是：

1. 校验管理员权限。
2. 对目标题目加 `lock:problem:{problemId}` 写锁。
3. 读取旧题目记录。
4. 将新 zip 解压到临时目录。
5. 校验文件和配置。
6. 构造新的 `Problem` 实体。
7. 更新 `problem` 表。
8. 删除旧的 `problem_tag`，再写入新的标签。
9. 将新测试数据上传到新的 `problem_key` 前缀。
10. 删除旧 `problem_key` 前缀的 MinIO 对象。
11. 失效题目缓存和相关比赛详情缓存。
12. 删除临时目录。

### 6.2 更新时不会改哪些东西

更新链路不会改题目可见性。  
更新链路不会直接决定公开/私密状态。  
如果要改 `visibility`，必须走单独的 `PATCH /problem/visibility`。

### 6.3 当前实现的关键点

#### 读写锁

更新使用 Redisson 的读写锁：

- 锁名：`lock:problem:{problemId}`
- 更新时拿写锁
- 查询题目和标签时拿读锁

这样做的目标是避免题面更新和缓存回源并发时读到中间态。

#### 新旧对象切换

更新时会生成一个新的 `problem_key`，把新文件上传到新的对象目录。成功后旧目录会删除，题目表中的 `problem_key` 指向新目录。

#### 版本号递增

更新时 `version = oldVersion + 1`。

#### 标签替换

更新采用全量覆盖策略：

- 先删旧标签
- 再插入新标签

不是局部 patch。

#### 作者字段

当前实现里，更新时也会把 `authorId` 写成当前登录用户 ID。这是代码里的实际行为，文档按实现记录，不做额外推断。

#### MinIO 删除失败的处理

旧目录删除失败时不会抛异常中断主流程，只记录日志。  
原因是此时数据库和新对象已经切换完成，再因为旧对象清理失败回滚数据库，反而会让表指针和对象存储重新失配。

### 6.4 更新失败时的补偿

如果在“更新数据库 + 写新标签 + 上传新对象”这一段中出现异常，当前实现会：

1. 删除新 `problem_key` 对应的 MinIO 前缀。
2. 继续抛出异常，让事务回滚。
3. 保持旧题目和旧对象指针不变。

也就是说，更新链路是有补偿回滚的，和创建链路相比更完整。

### 6.5 更新后缓存失效

更新成功后会失效：

- `problem:{problemId}`
- `problem_tags:{problemId}`
- 所有引用该题目的 `contest_detail:contest:{contestId}`

不会显式批量删除：

- `problem_page:current:{current}:size:{size}`

原因是公开题目分页依赖短 TTL，允许短时间陈旧。

不会删除：

- `contest_problem:contest:{contestId}`

因为比赛题目编排只缓存 `problemId + problemIndex`，题面更新不会改变编排结构。

## 7. 查询链路

题目查询不是单一接口，而是统一进入 `ProblemService.query(ProblemQueryRequest)`。

### 7.1 三个查询入口

#### 1. `/problem/{id}`

按题目 ID 查询，不携带比赛上下文。

- public 题可匿名查看。
- private 题仅管理员可查看。

#### 2. `/contest/{contestId}/problem/{index}`

按比赛内题号查询。

流程是：

1. 先从 `ContestCacheService.getContestProblems(contestId)` 取比赛题目编排。
2. 用 `index` 找到真实 `problemId`。
3. 携带 `contestId` 进入统一查询。

#### 3. `/agent/problem/{id}`

Agent 查询入口。

- 这个入口会把 `isAgent = true`。
- 但它不携带 `contestId`。
- 因此非管理员 Agent 不能借这个接口直接查看比赛中的 private 题。

### 7.2 统一查询流程

统一查询的具体步骤是：

1. 从 `ProblemCacheService.getProblem(problemId)` 获取题目元数据。
2. 如果题目不存在，抛 `PROBLEM_NOT_FOUND`。
3. 调用 `ProblemPermissionChecker.checkAccessPermission(...)` 判断是否允许访问。
4. 如果有完整访问权限，读取标签缓存并拼成完整 `ProblemVO`。
5. 如果只有比赛上下文中的受限访问权限，只返回裁剪后的题面字段。

### 7.3 题目元数据缓存

`ProblemCacheService.getProblem(problemId)` 的实现是：

```text
Redis 读取 problem:{problemId}
  -> 未命中时通过 lock:cache:{key} 防击穿
  -> 再次检查缓存
  -> 回源数据库 ProblemQueryService.select(problemId)
  -> 写回缓存
```

这里的 TTL 名称是 `problem`。

### 7.4 标签缓存

`ProblemCacheService.getProblemTags(problemId)` 也是同样的回源模式，只是缓存值是 `List<String>`，TTL 名称是 `problem-tags`。

### 7.5 公开题目分页缓存

`ProblemCacheService.getProblemPage(current, size)` 会缓存到：

```text
problem_page:current:{current}:size:{size}
```

缓存内容只包含公开题目的摘要项：

- `id`
- `name`
- `difficulty`

TTL 名称是 `problem-list`，是短 TTL，允许写入后短时间内旧列表存在。

### 7.6 管理员分页查询

`/admin/problem` 和 `/admin/problem/mine` 都不走 Redis 缓存，直接查数据库。

原因很明确：

- 后台管理入口不需要依赖公开题目缓存。
- 管理员分页的字段和排序要求与公开列表不同。
- `mine` 还要按当前登录管理员的 `authorId` 收敛范围。

## 8. 权限模型

题目模块里最容易混淆的是“查看权限”和“提交权限”。这两个权限不是一回事。

### 8.1 查看权限

`ProblemPermissionChecker.checkAccessPermission(...)` 的判断顺序是：

1. `public` 题直接放行。
2. 已登录的管理员或超级管理员直接放行。
3. 非 `public` 且不是 `private` 的值直接判非法并抛 `FORBIDDEN`。
4. `private` 题必须携带合法比赛上下文。

### 8.2 private 题的查看条件

一个普通用户要查看比赛中的 private 题，必须满足：

1. `contestId` 不能空。
2. `contest` 必须存在。
3. 这道题必须属于这个比赛。
4. 当前时间必须在 `[startTime, endTime)` 之间。

注意这里不要求报名，只要求处于合法比赛上下文。  
也就是说，“看题”和“提交”是拆开的。

### 8.3 受限题面

当用户只是通过比赛上下文查看 private 题，但没有完整访问权限时，返回内容只保留做题必需字段，不返回题解、标签、作者、版本等信息。

### 8.4 提交权限

`ProblemPermissionChecker.checkSubmitPermission(...)` 会被 `submit` 模块复用。它的规则比查看更严格：

1. 先要求用户登录。
2. 管理员或超级管理员直接放行。
3. `public` 题可提交，但仍要求登录。
4. `private` 题必须先满足比赛上下文合法。
5. 如果 `isAgent = true`，普通用户的比赛 private 题提交会被拒绝。
6. 用户必须已经报名这个比赛。

也就是说：

- `public` 题：匿名可看，提交必须登录。
- `private` 题：看题可以依赖比赛上下文，提交还要报名。
- Agent 不能绕过比赛中的 private 题提交限制。

### 8.5 角色判断

管理员判断不是直接读字符串，而是通过 `AuthService.getRole(userId)` 做统一角色读取，再判断是否为：

- `admin`
- `super_admin`

### 8.6 无效可见性

如果 `visibility` 不是 `public` / `private`，当前实现会记录日志并抛 `FORBIDDEN`。这属于服务端数据异常，而不是正常业务分支。

## 9. 请求校验

题目请求参数由 [ProblemRequestChecker](../problem/service/ProblemRequestChecker.java) 统一校验。

### 9.1 创建 / 更新 zip 校验

`checkProblemFiles(Path dir)` 会检查：

1. `config.yaml` 是否存在。
2. `config.yaml` 是否能解析成 `ProblemConfig`。
3. 必填字段是否齐全。
4. `checker` 是否支持。
5. `difficulty` 是否在 `1..3500`。
6. `name` 是否不超过 50 个字符。
7. `time_limit` 和 `memory_limit` 是否大于 0。
8. `tags` 是否都在允许枚举里。
9. `statement.md` 是否存在。
10. `checker = special` 时 `checker.cpp` 是否存在。
11. `input/1.in` 和 `answer/1.ans` 是否同时存在。
12. `input/i.in` 与 `answer/i.ans` 是否连续成对存在。

### 9.2 分页请求校验

`checkProblemPageRequest(current, size)`：

- `current >= 1`
- `1 <= size <= 100`

### 9.3 排序参数校验

`checkProblemPageOrder(order)` 只接受：

- `asc`
- `desc`

### 9.4 失败码

这个模块里比较常见的业务码有：

- `UNZIP_FAILED`
- `CONFIG_FILE_INVALID`
- `FILE_MISSING`
- `READ_FILE_FAILED`
- `TOO_MANY_REQUESTS`
- `PROBLEM_NOT_FOUND`
- `PROBLEM_REQUEST_INVALID`
- `FORBIDDEN`

## 10. 文件处理

题目 zip 的解压和本地文件读取统一由 [FileService](../problem/storage/FileService.java) 处理。

### 10.1 解压

`unzip(MultipartFile file, Path destDir)` 使用 `ZipInputStream` 解压到目标目录。

这里有一个重要安全点：

- 解压前会把目标路径 `normalize()`
- 会检查 zip entry 的路径是否仍然落在目标目录之内
- 如果发现 `../` 这种路径逃逸，会抛 `UNZIP_FAILED`

这可以防止 zip 包通过路径穿越覆盖服务端其他文件。

### 10.2 读取文本

`readTextFile(Path path)` 用于读取：

- `statement.md`
- `solution.md`
- `config.yaml` 由 SnakeYAML 读取

### 10.3 删除临时目录

`deleteDirectory(Path dir)` 用 `FileSystemUtils.deleteRecursively` 做幂等删除。

创建和更新链路都依赖这个方法在 `finally` 里清理临时目录。

## 11. Persistence 层职责

题目模块的 persistence 层是围绕“表级职责”拆开的。

### 11.1 `ProblemQueryService`

负责只读查询：

- `select(id)`：按 ID 查单题。
- `selectByIds(ids)`：按 ID 集合批量查题。
- `selectNamesByIds(ids)`：只查 `id` 和 `name`，给比赛详情和提交展示名用。
- `selectPublicPage(current, size)`：公开题分页，只查 `public`，按 `id ASC`。
- `selectAdminPage(current, size, order)`：管理员分页，全量查题，返回 `id` / `name` / `visibility`。
- `selectAdminPageByAuthor(current, size, authorId, order)`：管理员本人创建题目的分页查询。

### 11.2 `ProblemUpdateService`

负责 `problem` 主表的写入：

- `insert(problem)`：插入题目。
- `update(id, problem)`：更新题目主体字段。
- `updateVisibility(id, visibility)`：更新可见性。

### 11.3 `ProblemTagQueryService`

负责按 `problemId` 查询标签列表。

### 11.4 `ProblemTagUpdateService`

负责标签写入和删除：

- `batchInsert(problemId, tags)`
- `batchDelete(problemId)`

这个类只处理标签表，不混写题目主表。

## 12. 缓存与锁

### 12.1 题目元数据缓存

题目元数据缓存的 key 是 `problem:{problemId}`，值是 `Problem`。

### 12.2 标签缓存

标签缓存的 key 是 `problem_tags:{problemId}`，值是 `List<String>`。

### 12.3 公开题列表缓存

公开题列表缓存的 key 是 `problem_page:current:{current}:size:{size}`，值是 `ProblemPageVO`。

### 12.4 回源锁

缓存未命中时，`RedisService.get(...)` 会自动使用：

```text
lock:cache:{cacheKey}
```

来防止缓存击穿。

### 12.5 题目读写锁

题目更新和题目读取共用：

```text
lock:problem:{problemId}
```

更新时用写锁，查询时用读锁。  
这样能避免题面、标签和缓存回源在并发下读到半写入状态。

### 12.6 缓存失效时机

题目更新或可见性修改成功后，会显式删除：

- `problem:{problemId}`
- `problem_tags:{problemId}`

题目更新后还会删除所有引用该题目的比赛详情缓存：

- `contest_detail:contest:{contestId}`

题目更新不会主动删除：

- `problem_page:current:{current}:size:{size}`

因为这个列表依赖短 TTL 自动更新。

## 13. 与 contest / submit 模块的联动

题目模块不是完全孤立的，它会被 `contest` 和 `submit` 模块复用。

### 13.1 比赛详情联动

`ContestCacheService.getContestDetail(...)` 会读取题目标题摘要。  
因此题目标题更新后，必须失效相关的比赛详情缓存，否则比赛详情页会长期显示旧题名。

### 13.2 比赛题目编排联动

`contest_problem:contest:{contestId}` 只缓存题目编排，不缓存题面内容。  
所以题面更新不会动这个缓存。

### 13.3 提交链路联动

`SubmitService.judge(...)` 会复用 `ProblemPermissionChecker.checkSubmitPermission(...)` 来判断提交权限。  
这意味着题目模块里的权限定义，会直接影响提交模块。

## 14. 当前实现里值得注意的细节

下面这些不是抽象设计，而是当前代码的实际行为：

1. 创建题目时 `visibility` 固定是 `private`。
2. 更新题目时不会修改可见性。
3. 更新题目时会递增 `version`。
4. 创建和更新都依赖临时目录，结束后清理本地文件。
5. 公开题列表依赖短 TTL，不做写后强删。
6. 管理员列表不走缓存，直接查库。
7. `ProblemVO` 的完整字段和受限字段不是同一个返回集。
8. `queryInContest` 只靠比赛题目编排缓存做题号映射。
9. 题目访问和题目提交是两套权限条件。
10. `ProblemPermissionChecker` 同时服务题目模块和提交模块。

## 15. 文档用途

本文件用于说明题目模块的导入、更新、查询、权限、缓存和存储链路。更细的类与接口实现对应下列源码文件：

- [ProblemController](../problem/controller/ProblemController.java)
- [ProblemService](../problem/service/ProblemService.java)
- [ProblemCacheService](../problem/service/ProblemCacheService.java)
- [ProblemPermissionChecker](../problem/service/ProblemPermissionChecker.java)
- [ProblemRequestChecker](../problem/service/ProblemRequestChecker.java)
- [FileService](../problem/storage/FileService.java)
