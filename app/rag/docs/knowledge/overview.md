# TenJudge Server 总览

`tenjudge-server` 是 TenJudge 在线评测系统的后端服务，负责用户鉴权、题目管理、比赛管理、提交落库、评测任务投递，以及围绕题目、比赛和提交形成的一整套数据一致性维护。

这份文档只覆盖项目级别的通用约定和全局实现方式。具体模块的业务规则、接口细节和链路说明分别收敛在：

- [Auth 模块](../auth/README.md)
- [Problem 模块](../problem/README.md)
- [Contest 模块](../contest/README.md)
- [Submit 模块](../submit/README.md)

## 1. 项目定位

TenJudge Server 是一个典型的单体 Java 后端，整体上围绕 OJ 场景的四条主业务线展开：

1. 用户认证与权限校验。
2. 题目导入、更新、查询与可见性控制。
3. 比赛元数据、题目编排、报名与榜单维护。
4. 提交落库、源码存储、消息投递和评测结果承接。

项目当前采用 Spring Boot 4 + Maven 构建，运行时依赖 PostgreSQL、Redis、Redisson、RabbitMQ、MinIO 和 Sa-Token。业务代码仍然保持单体组织方式，但在内部按模块拆分 `service`、`persistence`、`infra`、`storage`、`mq` 等职责层，避免把基础设施逻辑直接堆到 Controller 中。

## 2. 启动与运行模型

### 2.1 应用入口

主启动类是 [TenjudgeServerApplication](../TenjudgeServerApplication.java)，核心特征如下：

- `@SpringBootApplication`：启用 Spring Boot 自动配置。
- `@EnableScheduling`：启用定时任务，用于榜单预热、榜单刷新等后台作业。
- `@EnableAsync`：启用异步能力，供部分后台流程异步执行。

应用启动后，HTTP 接口由 Spring MVC 对外提供，API 文档依赖 Springdoc OpenAPI 暴露。

### 2.2 运行时依赖

核心运行时依赖集中在 `pom.xml` 中：

- `spring-boot-starter-webmvc`：HTTP 接口。
- `springdoc-openapi-starter-webmvc-ui`：OpenAPI / Swagger UI。
- `mybatis-plus-spring-boot4-starter`：数据库访问与 ORM 支撑。
- `spring-boot-starter-data-redis`：Redis 访问。
- `spring-boot-starter-cache`：Spring Cache。
- `redisson-spring-boot-starter`：分布式锁。
- `spring-boot-starter-amqp`：RabbitMQ。
- `minio`：对象存储。
- `sa-token-spring-boot3-starter` 与 `sa-token-redis-template`：登录态与鉴权。

项目目标 JDK 版本为 Java 21。

## 3. 分层结构

项目采用“Controller -> Service -> Persistence / Infra / Storage / MQ”的组织方式。这里的层次不是纯粹教科书式分层，而是围绕业务编排和基础设施封装来划分职责。

### 3.1 Controller

`controller` 负责：

- 接收 HTTP 请求。
- 绑定请求参数。
- 调用 `service` 完成业务编排。
- 统一返回 `Result<T>`。

Controller 不承载复杂的业务判断，不直接操作 Redis、MinIO、MQ 或数据库写入细节。

### 3.2 Service

`service` 是核心业务编排层，处理：

- 权限判断。
- 事务边界。
- 多表写入顺序。
- Redis 缓存读写和失效时机。
- MinIO 上传、删除、回滚。
- MQ 消息发送。
- 分布式锁加锁和释放。

当前项目中，跨模块的一致性规则基本都落在 service 层。

### 3.3 Persistence

`persistence` 层面向 service，负责：

- 某一张表或一类关系表的查询。
- 某一张表或一类关系表的更新。
- 保持 SQL 和对象映射足够聚焦。

按照当前约定，写入职责默认按表拆分。单个 `*UpdateService` 不应长期承担多表混合写入，复杂链路应由上层 service 编排多个 persistence 服务完成。

### 3.4 Infra

`infra` 是跨模块基础设施封装层，当前主要包括：

- `RedisService`：统一缓存读写、空值缓存、缓存击穿锁、显式删除。
- `MinioService`：统一对象存储上传、下载、读取和删除。

业务代码不应直接散落使用 `RedisTemplate` 访问业务缓存。

### 3.5 Storage

`storage` 一般保留模块内的本地文件处理逻辑，尤其是题目 zip、临时目录和本地校验类操作。需要注意的是，本项目里对象存储能力统一收敛在 `infra`，`storage` 只保留与业务边界强相关的本地文件辅助逻辑。

### 3.6 MQ

`mq` 主要承接提交后的异步评测消息投递与消费处理。提交链路会先完成落库和源码上传，再投递消息给评测系统或评测消费者。

## 4. 统一返回与异常模型

### 4.1 返回体

全局响应统一使用 [Result](../common/Result.java)，字段为：

- `code`
- `message`
- `data`

Controller 层只返回：

- `Result.success(...)`
- `Result.success()`

不要在 Controller 内手动拼接错误响应格式。

### 4.2 错误码

统一错误码定义在 [Code](../common/Code.java) 中，涵盖：

- 通用成功与系统错误。
- 用户认证与注册相关错误。
- 题目导入、文件处理与题目查询错误。
- 比赛请求、报名和榜单相关错误。
- 提交请求和提交查询错误。

新增业务失败类型时，应优先补充 `Code`，然后通过 `BizException` 抛出。

### 4.3 异常出口

全局异常由 [GlobalExceptionHandler](../exception/GlobalExceptionHandler.java) 统一处理：

- `BizException`：返回对应业务码和业务消息。
- 其他异常：统一按系统异常处理，返回 `Code.SERVER_ERROR`。

这意味着业务代码中不应随意 catch 后吞掉异常，也不要自行扩展其他异常响应分支。

### 4.4 文案约定

项目中异常和日志文案保持明确语言区分：

- `BizException` 的 `message` 使用英文。
- `RuntimeException` 及其他非业务异常的异常消息，以及直接写入日志的 `msg` / `message`，使用中文。

这套约定是为了保证对外业务码稳定，同时让服务端日志更适合排障。

## 5. 数据库概览

数据库统一维护在 [src/main/resources/db/schema.sql](../../../../../resources/db/schema.sql) 中。后续涉及表结构变更时，应同步修改该文件。

### 5.1 核心表

#### `users`

用户基础信息表，承载：

- 用户 ID。
- 用户名与密码。
- 创建时间。
- 角色。
- 评分信息。
- 邮箱。
- 简介。
- 已解决题目数。

#### `problem`

题目主表，承载：

- 作者。
- 可见性。
- checker 类型。
- 时间和内存限制。
- 题目名称、题面、题解。
- 难度。
- 对象存储指针 `problem_key`。
- 版本号。
- 测试点数量。

#### `problem_tag`

题目标签关系表，使用 `problem_id + tag` 作为主键。

#### `contest`

比赛主表，承载：

- 比赛名称。
- 开始时间、结束时间。
- 封榜时间。
- 榜单解除封榜刷新时间。
- 每次错误提交罚时。

#### `contest_problem`

比赛题目编排关系表，保存比赛与题目之间的映射，以及题号标识 `problem_index`。

#### `contest_participant`

比赛参赛者快照表，存储：

- 用户在某场比赛中的报名身份。
- 过题数、罚时、最后一次有效 AC 时间。
- 按题目维度维护的结果快照 JSON。

#### `submission`

提交主表，记录：

- 提交类型。
- 题目和比赛关联。
- 提交者。
- 是否为 Agent 提交。
- 提交时间。
- 语言。
- 测评状态。
- 时间、内存和整体信息。

#### `submission_detail`

提交测试点级别明细表，按 `submission_id + test_case_id` 组织。

### 5.2 表设计上的几个重要点

1. `problem` 使用 `problem_key` 指向 MinIO 的对象目录，而不是把测试数据直接存数据库。
2. `contest_participant.problem_results` 使用 `jsonb` 保存题目结果快照，便于榜单按题目维度重建。
3. `submission` 和 `submission_detail` 分离，主表存整体状态，明细表存每个测试点的执行结果。
4. 多个查询场景依赖索引优化，尤其是公开题目分页、比赛列表、提交列表和参赛者查询。

## 6. Redis 概览

Redis 在项目中承担三类职责：

1. 业务缓存。
2. 空值缓存防穿透。
3. 分布式锁和定时任务互斥。

Redis 统一由 [RedisService](../infra/RedisService.java) 管理，TTL 名称统一从 [AppCacheProperties](../config/AppCacheProperties.java) 读取。

### 6.1 TTL 管理

缓存 TTL 采用“名称驱动”的方式配置，避免业务代码直接硬编码 `Duration`。

当前默认 TTL 包括：

- `user-role`
- `problem`
- `problem-tags`
- `problem-list`
- `contest-problem`
- `contest-detail`
- `contest-list`
- `null-value`
- `spring-cache-default`

如果 yml 未显式配置，对应默认值由 `AppCacheProperties` 提供。

### 6.2 统一缓存读写方式

`RedisService` 提供三种常用能力：

- `get(key, clazz, ttlName, loader)`：带回源的缓存读取入口，内部带缓存击穿锁和空值缓存。
- `getValue(key, clazz)` / `set(key, value, ttlName)`：用于简单值缓存。
- `delete(key)`：用于数据库写入成功后的显式失效。

业务模块不应直接绕过这个封装去操作业务缓存。

### 6.3 常见 key 类型

当前项目显式使用的 Redis key 大致分为以下几类：

#### 用户角色缓存

- `user:role:{userId}`

用于减少高频权限检查中的数据库查询。

#### 题目缓存

- `problem:{problemId}`
- `problem_tags:{problemId}`
- `problem_page:current:{current}:size:{size}`

分别用于题目元数据、题目标签、公开题目分页列表。

#### 比赛缓存

- `contest_problem:contest:{contestId}`
- `contest_detail:contest:{contestId}`
- `contest_page:current:{current}:size:{size}`

分别用于比赛题目编排、比赛详情聚合、比赛分页公共数据。

#### 榜单相关缓存

- `contest:{contestId}:rank`
- `contest:{contestId}:participant:{userId}:detail`
- `contest:{contestId}:exist`

分别用于榜单排名、参赛者行数据和榜单可用性标记。

#### 锁相关 key

- `lock:problem:{problemId}`
- `lock:cache:{cacheKey}`
- `lock:contest:{contestId}:board-preload`
- `lock:contest:{contestId}:board-refresh`
- `lock:contest:{contestId}:user:{userId}:board`

分别用于题目更新、缓存回源、榜单预热、榜单刷新和单用户榜单重算互斥。

### 6.4 缓存策略概览

项目中的缓存策略有几个统一原则：

1. 读取优先缓存，缓存未命中才回源。
2. 回源过程加锁，避免缓存击穿。
3. 空结果写入短 TTL 空值缓存，避免穿透。
4. 写入链路在数据库成功后显式失效相关缓存。
5. 与对象存储、MQ、分布式锁相关的一致性动作由 service 编排，不放在 Controller。

## 7. 对象存储概览

对象存储统一通过 [MinioService](../infra/MinioService.java) 使用，避免业务代码直接拼接 MinIO 客户端调用。

MinIO 主要承担两类数据：

1. 题目相关文件。
2. 提交源码文件。

### 7.1 题目对象

题目对象目录通常以 `problem/<problem_key>/` 组织，内部包含：

- 输入测试点。
- 标准答案。
- 可选的 checker 程序。

`problem_key` 对应数据库中的对象指针，题目更新时会通过“新对象目录 + 数据库切换指针”的方式维持一致性。

### 7.2 提交对象

提交源码通常存放在：

- `submission/<submission_id>/code`

查询提交详情时，会从 MinIO 读取源码内容。若数据库存在提交记录但源码对象读取失败，按系统异常处理，不掩盖数据不一致。

## 8. 消息队列概览

RabbitMQ 主要用于提交后的异步评测流程。

当前提交链路的一般顺序是：

1. 权限检查。
2. 提交落库。
3. 源码上传 MinIO。
4. 发送 MQ 消息。

这样做的原因是：

- 保证数据库先记录提交主键，后续评测结果可以稳定回写。
- 保证源码文件先持久化，消费者拿到消息后即可读取。
- 通过消息解耦提交请求与实际判题耗时。

## 9. 定时任务与后台作业

项目启用了 `@EnableScheduling`，所以有明确的后台定时作业场景。当前比较重要的后台逻辑主要是：

- 榜单预热。
- 已结束比赛的榜单刷新。
- 其他需要定时检查的业务扫描任务。

这些作业通常会配合 Redis 锁做多实例互斥，避免同一场比赛在多个实例上重复处理。

## 10. 鉴权与登录态

项目使用 Sa-Token 管理登录态，并在 `auth` 模块中封装统一鉴权入口。

鉴权的核心约定是：

- 普通请求、管理员请求、超级管理员请求最终都走后端统一权限校验。
- 业务代码优先通过统一的 `AuthService` 完成登录和角色判断。
- 用户角色会被缓存到 Redis，减少重复数据库访问。

在实际业务中，题目查看权限、比赛管理权限、提交查看权限都依赖统一鉴权入口，不允许在多个接口里复制散落规则。

## 11. 模块之间的关系

### 11.1 Auth

负责登录、注册、角色维护和统一权限检查。它是其他模块进行“当前用户是谁、能做什么”的入口。

### 11.2 Problem

负责题目导入、题目更新、题目查看、权限判断、文件校验和 MinIO 一致性。

### 11.3 Contest

负责比赛创建与更新、题目编排、报名与取消报名、比赛详情与榜单相关逻辑。

### 11.4 Submit

负责提交创建、提交详情、提交列表、源码存储和 MQ 投递。

### 11.5 Common / Config / Infra

这些是全局基础能力：

- `common` 提供响应体、错误码、基础枚举。
- `config` 提供 Redis、MinIO、RabbitMQ、CORS、MyBatis-Plus 等基础配置。
- `infra` 提供 Redis 和 MinIO 的统一访问封装。

## 12. 修改项目时的全局注意点

下面这些规则属于项目级别的“共识”，写新功能或改旧流程时都要优先遵守：

1. 对外接口统一返回 `Result<T>`。
2. 返回前端的数据对象统一使用 `VO`。
3. 请求对象统一使用 `*Request` 命名。
4. 业务失败抛 `BizException`，不要用无语义的成功返回掩盖错误。
5. Redis 业务缓存统一经过 `RedisService`。
6. MinIO 读写统一通过 `MinioService`。
7. 涉及数据库、对象存储、消息队列、分布式锁的流程，优先在 service 层编排。
8. 修改数据库结构时必须同步更新 `schema.sql`。
9. 修改缓存 key 或 TTL 名称时，需要同步检查根 `README.md` 的 Redis 小节。
10. 涉及题目和提交时，要同时关注数据库、对象存储、Redis 锁和 MQ 链路的一致性。

## 13. 文档用途

本文件用于说明项目整体设计、模块分工、基础设施职责以及全局约束。更细的问题可继续查看对应模块文档：

- 认证与用户：`auth/README.md`
- 题目导入、更新与查看：`problem/README.md`
- 比赛管理、报名和榜单：`contest/README.md`
- 提交、源码与评测链路：`submit/README.md`
