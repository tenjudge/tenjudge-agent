# Contest 比赛模块

`contest` 模块负责比赛元数据、题目编排、报名与取消报名、比赛详情、比赛分页列表，以及最核心的榜单预热和榜单重算。这个模块的复杂度不在“比赛信息能不能存下来”，而在于比赛一旦开始，题目编排、提交记录、封榜规则、缓存状态和最终榜单就会彼此牵连，因此它必须同时协调 PostgreSQL、Redis、Redisson、以及提交模块发送过来的评测结果消息。

本文档按“模块职责 -> 接口与数据模型 -> 比赛生命周期 -> 题目编排与比赛详情 -> 报名链路 -> 榜单系统 -> 缓存与定时任务 -> 实现边界”的顺序展开。榜单部分会写得更细，因为这里是整个模块最容易出错、也最容易被误解的地方。

相关代码入口如下：

- [ContestController](../contest/controller/ContestController.java)
- [ContestService](../contest/service/ContestService.java)
- [BoardService](../contest/service/BoardService.java)
- [ContestCacheService](../contest/service/ContestCacheService.java)
- [ContestRequestChecker](../contest/service/ContestRequestChecker.java)
- [ContestQueryService](../contest/persistence/ContestQueryService.java)
- [ContestUpdateService](../contest/persistence/ContestUpdateService.java)
- [ContestProblemQueryService](../contest/persistence/ContestProblemQueryService.java)
- [ContestProblemUpdateService](../contest/persistence/ContestProblemUpdateService.java)
- [ContestParticipantQueryService](../contest/persistence/ContestParticipantQueryService.java)
- [ContestParticipantUpdateService](../contest/persistence/ContestParticipantUpdateService.java)
- [Contest](../contest/entity/Contest.java)
- [ContestProblem](../contest/entity/ContestProblem.java)
- [ContestParticipant](../contest/entity/ContestParticipant.java)

## 1. 模块职责

比赛模块不是单纯的“比赛 CRUD”，它更像是一个围绕比赛状态机运转的编排中心。创建比赛只是起点，真正重要的是比赛开始前能否正确展示题目标题，比赛进行中能否正确控制报名和封榜，比赛结束后能否把封榜期间的提交重新纳入正式榜单，并且在这些变化发生时保持 Redis、数据库和消息消费侧的视图一致。

从职责上看，这个模块至少承担五层工作。第一层是比赛元数据管理，包括比赛名称、开始时间、结束时间、封榜时间和罚时配置。第二层是题目编排管理，也就是一场比赛里有哪些题、题号怎么标、更新时如何整体替换。第三层是报名关系管理，既要支持报名，也要支持取消报名，还要保证重复请求幂等。第四层是比赛详情和比赛分页列表的缓存编排，既不能把用户态数据污染进公共缓存，也不能让题目标题变化后比赛详情长期陈旧。第五层也是最重的部分，就是榜单系统：如何把评测结果转成 `contest_participant` 的快照，如何将快照同步到 Redis，如何预热未来比赛的榜单，如何在比赛结束后解除封榜并重算整场榜单。

## 2. 对外接口

`ContestController` 暴露的接口都挂在 `/contest` 下，接口覆盖比赛列表、比赛详情、榜单分页、新建比赛、更新比赛、报名和取消报名。这里的关键点是：列表和详情大部分是公开接口，但管理类写操作依赖管理员权限；榜单是公开查看，但展示前要先过比赛开始时间检查；报名和取消报名必须依赖当前登录态，而不是前端传入的用户 ID。

| 方法 | 路径 | 作用 | 是否要求登录 |
| --- | --- | --- | --- |
| `GET` | `/contest` | 分页查询比赛列表 | 否 |
| `GET` | `/contest/{contestId}` | 查询比赛详情和题目摘要 | 否 |
| `GET` | `/contest/{contestId}/board` | 分页查询比赛榜单 | 否 |
| `POST` | `/contest` | 新建比赛 | 是，管理员 |
| `PUT` | `/contest` | 更新比赛和题目编排 | 是，管理员 |
| `POST` | `/contest/register` | 报名比赛 | 是 |
| `DELETE` | `/contest/register` | 取消比赛报名 | 是 |

比赛列表返回 `ContestPageVO`，其中 `records` 是 `ContestListItemVO`。比赛详情返回 `ContestDetailVO`，其中包含比赛元数据和按题号排序的 `ContestProblemBriefVO` 列表。榜单返回 `BoardPageVO`，它会同时带上题目列、当前页榜单行和分页元数据。新建比赛返回 `CreateContestVO`，只给出新比赛 ID。更新比赛和报名相关接口都返回空成功结果。

列表接口的分页参数沿用统一约定：`current` 默认 1，`size` 默认 30，最大 100；榜单分页的 `size` 默认 50，最大 100。比赛创建和更新里的时间字段使用 `LocalDateTime`，在接口文档和示例里统一按 ISO 8601 的 `yyyy-MM-dd'T'HH:mm:ss` 形式表达。

## 3. 数据模型

比赛模块的数据库设计比较紧凑，但每张表都承担着明确职责。`contest` 是主表，存放比赛名称、开始时间、结束时间、封榜时间、榜单解除封榜刷新时间，以及每次错误提交的罚时。`contest_problem` 存放比赛题目编排关系，它不仅保存题目 ID，还保存比赛内部的题号标识 `problem_index`，这意味着同一场比赛里一个题目和一个题号都不能重复。`contest_participant` 则是整个榜单系统的持久化快照表，里面存放报名用户的用户名快照、过题数、罚时、最后一次有效 AC 的比赛分钟数，以及按题目维度存储的 JSONB 结果快照。

`contest_participant.problem_results` 是这个模块里最关键的字段之一。它使用 `problemId` 作为 JSONB key，而不是题号字符串，因为比赛题目编排可能变化，但题目 ID 是稳定的。对应的 Java 对象是 [ProblemResultDTO](../contest/dto/ProblemResultDTO.java)，里面保存四个核心字段：`accepted`、`acceptedAt`、`wrongAttemptsBeforeAc` 和 `attemptsAfterFreeze`。这里的 `acceptedAt` 不是时间戳，而是“从比赛开始到首次通过时经过了多少分钟”，这样才能直接参与 ICPC 风格的罚时计算和同分排序；`attemptsAfterFreeze` 则只记录封榜后的有效提交次数，用于比赛结束后解除封榜时还原完整状态。

表和索引也和查询场景严格对应。`contest(start_time DESC, id DESC)` 支撑比赛分页和即将开始比赛扫描；`contest_participant(user_id, contest_id)` 支撑当前用户在一页比赛里的报名态查询；`contest_participant(contest_id, solved_count DESC, penalty ASC, last_accepted_time ASC)` 则对应榜单分页的排序需求。`contest_problem(contest_id, problem_id)` 和 `contest_problem(contest_id, problem_index)` 的唯一约束，则从数据库层兜住了题目编排的唯一性。

## 4. 比赛生命周期

比赛在这个系统里不是一个静态记录，而是会经历几个清晰阶段。创建之后，比赛只是一个带开始时间和结束时间的元数据对象；在开始之前，普通用户可以看到比赛列表，但不能提前看题目标题，比赛详情页也会被阻断；开始之后，比赛变成一个可报名、可访问题面、可刷榜的运行中对象；如果设置了 `freezeTime`，在封榜之后榜单会进入“只记录不展示”的阶段，封榜后的提交不会影响当前可见排行，但会被持久化并在比赛结束后进入最终快照；比赛结束后，系统会把封榜期内的有效提交重新纳入正式榜单，形成解除封榜后的完整结果。

这里有一个很重要的实现细节：比赛结束前，榜单展示的是“封榜视图”，封榜后的提交只累计到 `attemptsAfterFreeze`，不改变可见排名字段；比赛结束后，定时任务会重新刷新整场榜单，这时封榜后的有效提交会正常进入 `contest_participant` 的正式快照。也就是说，封榜不是丢数据，而是把数据从“可见排名”里暂时隔离出去，等比赛结束再统一落成最终结果。

## 5. 新建与更新

新建比赛的入口是 `POST /contest`。`ContestService.createContest()` 会先通过 `authService.checkAdmin()` 确认当前用户是管理员或超级管理员，然后由 `ContestRequestChecker` 校验请求字段，再把比赛名称、开始时间、结束时间、封榜时间和罚时整理成 `Contest` 实体写入数据库。这里有两个默认行为必须记住：比赛名称会先 `trim()`，避免前后空格进入数据库；`penaltyPerWrong` 如果前端没传，后端会按 `0` 入库。创建比赛只负责元数据，不负责题目编排，题目列表是在更新阶段单独维护的。

更新比赛的入口是 `PUT /contest`，它比创建复杂得多，因为这里不仅会改元数据，还会整体覆盖比赛题目编排。`ContestService.updateContest()` 的逻辑是先检查比赛是否存在，再校验题目编排请求中每个题目是否真实存在，然后把新的比赛元数据写库，最后用全量覆盖的方式替换 `contest_problem`。这意味着 `contestProblems` 不是 patch 语义，而是“整场比赛重新编排”的语义：你传空列表，就表示清空当前题目；你传一个新列表，就用新列表完全替换旧列表。这个行为不能误解成局部增删，否则会破坏比赛题目顺序和榜单映射。

更新比赛还有一个和榜单强相关的动作：如果开始时间、结束时间或封榜时间发生变化，系统会清空 `board_refreshed_at`，并且在比赛已经开始的情况下立即重算当前榜单快照。这样做的原因很直接，比赛边界一旦改变，旧的封榜视图就不再可靠，必须尽快把当前可见榜单重建到新边界下。题目编排和时间边界的变更完成后，比赛相关缓存也会被统一失效，后续请求再重新回源。

## 6. 比赛列表与详情

比赛列表接口 `GET /contest` 是公开的，且并不把登录态信息直接写进缓存。`ContestCacheService.getContestPage()` 只缓存所有用户共享的公共比赛元数据，也就是比赛 ID、名称、开始结束时间和封榜时间；随后 `ContestService.queryContestPage()` 会在每次请求时按当前登录态拼接 `ended` 和 `registered` 两个字段。`ended` 是通过当前时间和比赛结束时间动态计算出来的，`registered` 则只在登录用户存在时才会去查参赛关系。这样设计的好处是公共缓存不会被用户态污染，也不会因为某个用户是否报名而产生多份缓存副本。

比赛详情接口 `GET /contest/{contestId}` 也是公开的，但它不是完全无条件开放。比赛开始前，普通用户和游客不能看到题目摘要列表，只有管理员和超级管理员可以提前查看；比赛开始后，无论是否登录都可以查看。这个限制的核心目的不是隐藏比赛存在，而是避免提前泄露题目标题和题目编号。实现上，`ContestService.queryContestDetail()` 先从 `contestCacheService.getContestDetail(contestId)` 取比赛详情聚合，再根据 `startTime` 和当前登录角色决定是否放行。比赛详情聚合缓存里包含比赛元数据和按题号排序的题目摘要，这些摘要来自题目模块的题目标题，因此题目标题更新时也必须把相关比赛详情缓存失效。

`ContestCacheService` 在这里扮演了一个中间层角色。它会缓存比赛题目编排 `contest_problem:contest:{contestId}`，再用这个编排去批量查询题目标题，最后拼成 `contest_detail:contest:{contestId}`。之所以要把题目编排和详情拆成两个缓存，是因为题目标题会变，而题目编排不一定变；编排和详情如果混在一起，任何题名修改都得重建整场比赛缓存，成本太高。现在的做法是把编排缓存得更久，把详情缓存得更短，并在题面变更时只针对引用该题目的 `contest_detail` 做失效。

## 7. 报名与取消报名

报名接口 `POST /contest/register` 和取消报名接口 `DELETE /contest/register` 都依赖当前登录态，后端不会接收前端传来的用户 ID。`ContestService.registerContest()` 会先确认比赛存在，再判断比赛是否还没结束；只要比赛未结束，就允许报名。这里的“允许”是按时间边界判断的，比赛一旦到达结束时间，就不再接受新报名。报名时如果该用户已经报名过，接口按幂等成功处理，不会因为重复请求而报错。

报名成功后，系统会把当前用户名快照写入 `contest_participant`，并把该用户的初始榜单行设置为过题数 0、罚时 0、最后一次 AC 时间 0。用户名快照的意义在于历史榜单不受后续改名影响。随后 `BoardService.handleRegister()` 会异步补写 Redis 榜单缓存，但这个补写不是强同步的，它依赖一个异步任务和短暂等待来避免与榜单预热任务竞态。当前实现里，这部分逻辑还保留了一个明确的 TODO，后续可以考虑用 Lua 或更强的原子操作来做得更稳，但现在的行为已经能满足正常路径。

取消报名的约束更严格，因为报名记录一旦进入比赛开始后的提交和榜单链路，就不能再被用户自己删掉。`ContestService.cancelRegisterContest()` 只允许在比赛开始前取消报名；比赛开始后会直接返回 `CONTEST_CANCEL_REGISTER_FAILED`。当比赛还没开始时，取消报名只会删除 `contest_participant` 关系，不影响比赛元数据、题目编排缓存，也不影响比赛详情缓存。对应的 Redis 删除由 `BoardService.handleUnregister()` 异步完成，如果榜单缓存已经失效，则不会再尝试恢复或重建。

## 8. 榜单系统

榜单是比赛模块最核心、也是最容易读偏的部分。它不是简单地把 `contest_participant` 表分页拿出来，而是由“数据库快照 + Redis 加速层 + MQ 驱动更新 + 定时任务预热/刷新”四个部分共同组成。可以把它理解成两层：`contest_participant` 是真相层，Redis 是加速层。真相层永远可以重建加速层，但加速层不能反向修改真相层。

### 8.1 榜单数据到底存在哪里

榜单的持久化快照存放在 `contest_participant` 表中，里面有 `solved_count`、`penalty`、`last_accepted_time` 和 `problem_results`。Redis 里则存放了三类数据：`contest:{contestId}:rank` 是 ZSET，只保存用户 ID 和排序分数；`contest:{contestId}:participant:{userId}:detail` 是 String，保存完整的 `ContestParticipant` 行快照；`contest:{contestId}:exist` 是一个存在性标记，表示当前榜单缓存处于“可用状态”。这三个 key 组合起来，构成了 Redis 侧的完整榜单缓存。

这里要特别注意，`exist` 才是榜单缓存模式的开关，而不是 ZSET 本身。因为 ZSET 的过期和 String 的刷新行为不完全一样，系统故意让 `exist` 比 ZSET 早十秒过期，避免 ZSET 还残留一点时间时，查询和定时任务误以为榜单缓存仍然可用。也就是说，`exist` 消失以后，系统就回退到数据库路径；哪怕 ZSET 还没完全清掉，也不会再被当成活跃榜单重新续命。

### 8.2 排名是怎么计算的

榜单排名遵循 ICPC 风格的三层排序：先比过题数，过题数越多越靠前；如果过题数一样，再比罚时，罚时越少越靠前；如果罚时还一样，再比最后一次有效 AC 的比赛分钟数，越早通过越靠前。这个规则既体现在数据库快照里，也体现在 Redis ZSET 的 score 公式里。ZSET 的 score 不是业务分数，而是一个单调可比较的排序编码：

```text
score = -solvedCount * 1_000_000_000_000L
      + penalty * 1_000_000L
      + lastAcceptedTime
```

因为 ZSET 是按 score 从小到大排序，所以过题数越多，score 越小；同分下罚时越少，score 越小；同罚时下通过越早，score 越小。这个编码方式的作用不是“算成绩”，而是把三层排序压成一个可以直接给 ZSET 用的数字。真正的业务数据仍然存在 `contest_participant` 里，ZSET 只是一个高性能排序索引。

### 8.3 题目结果是怎么累积出来的

`ContestParticipant` 上的三个方法决定了单题结果如何影响整场榜单。`markRejected(problemId)` 只在题目尚未通过时增加 `wrongAttemptsBeforeAc`，已经 AC 的题不会再被错误提交污染。`markAccepted(problemId, acceptedAt, penaltyPerWrong)` 只在第一次 AC 时生效，它会把题目标记为通过、写入首次通过分钟数，同时更新 `solvedCount`、`penalty` 和 `lastAcceptedTime`；如果一题已经通过，再次 AC 不会重复计数。`markFrozenAttempt(problemId)` 只用于封榜后的有效提交，它只增加 `attemptsAfterFreeze`，不会改 `solvedCount`、`penalty` 或 `lastAcceptedTime`。这三个方法配合起来，才能把一串提交正确压成一行榜单快照。

`acceptedAt` 使用的是“比赛开始到首次通过的分钟数”，不是绝对时间戳。这样做的原因是榜单排序和罚时都是围绕比赛相对时间来算的，而不是围绕真实世界时间来算。`lastAcceptedTime` 也是同样的分钟粒度，它保存的是该选手最后一次首次通过题目的比赛分钟数，用来做同分下的第三层排序。`problem_results` 里存的是每题细节，而 `solvedCount`、`penalty`、`lastAcceptedTime` 则是整行聚合字段，二者需要同时更新，不能只改其一。

### 8.4 提交结果怎么进榜单

评测完成后的提交消息会进入 RabbitMQ 队列 `tenjudge.judge.complete.queue`，然后由 `submit/mq/Listener` 消费并调用 `BoardService.handleJudgeResult(submissionId)`。这条链路很重要，因为榜单更新不是在提交接口里同步完成的，而是由 MQ 驱动的异步流程来完成。这样做的好处是提交接口不需要等待榜单重算，缺点是榜单更新存在短暂的最终一致性窗口。

`handleJudgeResult()` 的处理方式不是“对当前提交做增量更新”，而是“拿到这个用户在这场比赛里的全部非 Agent 提交，按提交时间和提交 ID 正序重放一遍”。这一点非常关键，它能消除评测完成消息乱序、重复投递或并发消费带来的状态错误。处理流程大致是：先读出提交记录，确认它属于某场比赛且提交时间没有晚于比赛结束时间，再确认结果不是 `SYSTEM_ERROR`，然后对同一场比赛同一用户加上 `lock:contest:{contestId}:user:{userId}:board` 锁，最后按时间顺序遍历这个用户的所有非 Agent 赛时提交，重新构造 `ContestParticipant` 的整行快照。

这里的“按提交时间和提交 ID 正序重放”不是细节，而是榜单正确性的核心。因为评测结果可能晚于提交产生，也可能乱序返回，如果直接按单条消息累加，很容易在封榜前后、重复 AC、重复 RE 的场景里把罚时和过题数算错。重放整条序列可以保证最终状态和历史提交序列一致。处理时还会主动忽略 `PENDING` 和 `SYSTEM_ERROR`，因为它们不应该进入榜单统计；另外，`SubmissionQueryService.selectBoardSubmissions(...)` 只会取 `isAgent = false` 的提交，所以 Agent 提交不会影响榜单。

### 8.5 封榜是怎么生效的

封榜的判断只看提交发生时间，不看评测完成时间。`submitTime >= freezeTime` 的提交，在比赛结束前会被视作封榜后提交，只增加 `attemptsAfterFreeze`，不影响当前可见榜单上的 `solvedCount`、`penalty`、`lastAcceptedTime` 和题目通过状态。这个规则在 `handleJudgeResult()` 和 `refreshContestBoard()` 里都一致执行。也就是说，只要比赛还没结束，封榜视图就必须保持对外一致，后来的有效提交只能被记录，不能改变当前排名。

比赛结束后，封榜会被解除。定时任务 `refreshEndedContestBoards()` 会扫描所有 `freeze_time` 非空、`end_time <= now` 且 `board_refreshed_at` 为空的比赛，然后逐场调用 `refreshContestBoard(contestId)`，最后把 `board_refreshed_at` 写成当前时间。这个动作的意义是：比赛正式结束后，把封榜期间累积的提交重新纳入正式榜单，形成最终快照。此时再次重放提交序列时，封榜期间的有效提交不再被隐藏，而会正常参与 `accepted`、`wrongAttemptsBeforeAc` 和 `penalty` 的计算。

### 8.6 榜单缓存是怎么预热的

榜单缓存不是等用户访问时才懒加载，而是在比赛开始前就提前预热。`preloadUpcomingContest()` 每三分钟跑一次，它会扫描未来五分钟内即将开始的比赛，并为这些比赛提前把 `contest:{contestId}:rank`、`contest:{contestId}:participant:{userId}:detail` 和 `contest:{contestId}:exist` 写入 Redis。这里的 TTL 使用 `app.cache-ttl.board`，当前配置默认是 24 小时；`exist` 的 TTL 会比 rank 少十秒，目的是在生命周期尾部留出一个很小的缓冲，避免查询和重建路径把已经快过期的缓存重新激活。

预热时还会用 `lock:contest:{contestId}:board-preload` 做多实例互斥。流程上先粗查 Redis，看 rank 或 exist 是否已经存在，如果存在就直接跳过；如果不存在，再加锁，加锁成功后会再次检查一次，确认没有别的实例抢先写入，然后才调用 `preloadCache(contestId)` 一次性把该场比赛所有参赛者写入缓存。这个两次检查不是多余的，它是为了避免多实例部署下重复全量写缓存，把相同比赛的榜单预热两次。

`preloadCache(contestId)` 自身也很直接：它从 `contest_participant` 里取出该比赛的所有参赛者，按当前快照给每个人写入 `rank` ZSET 和 `participant detail` String，然后给 rank 设置 TTL，再把 `exist` 标记成 true。这里的 `rank` 和 `detail` 实际上都是从数据库快照派生出来的缓存，真正的业务真相仍然是 `contest_participant` 表。

### 8.7 用户报名后为什么榜单会补写缓存

比赛报名成功后，`ContestService.registerContest()` 会把报名关系落库，然后异步调用 `BoardService.handleRegister(contestId, participant)`。这一步不是强同步，因为报名和榜单预热可能并发发生，所以系统先让注册请求返回，再在后台做榜单补写。`handleRegister()` 里故意 sleep 1 秒，是为了给预热任务留出时间，避免“预热刚读完数据库、还没写 ZSET，报名补写又抢先把缓存状态搞乱”的竞态。

报名补写的逻辑也有一个非常实际的保护：它只在 `contest:{contestId}:exist` 仍然存在时才会写缓存，并且会先检查 ZSET 里是否已经有这个用户。也就是说，如果榜单缓存已经过期，报名补写不会试图把已经失效的榜单重新续命；如果用户已经在缓存里，也不会重复写。当前代码里有一个明确的 TODO，后续可以考虑用 Lua 把“检查存在 -> 判断是否已有用户 -> 写缓存”做成原子操作，但现有实现已经能覆盖常规路径。

取消报名后，`BoardService.handleUnregister(contestId, userId)` 会在榜单缓存仍然活跃时把这个用户从 ZSET 和详情缓存里删掉，避免榜单里继续出现已经取消报名的人。如果榜单缓存本身已经失效，这个异步删缓存动作就直接跳过。这里的整体思路和报名补写一样，都是以数据库为准、以缓存为加速层，不做反向复活。

### 8.8 榜单分页是怎么查询的

`GET /contest/{contestId}/board` 走的是 `BoardService.queryBoardPage()`。这个接口不要求登录，但在比赛开始前不会展示榜单；它会先从 `contestCacheService.getContestDetail(contestId)` 取比赛详情，如果比赛不存在就返回 `CONTEST_NOT_FOUND`，如果当前时间还在开始时间之前，就返回 `CONTEST_NOT_STARTED`。这个判断复用了详情缓存，避免为了验证开始时间单独再查一次数据库。

真正查询榜单时，系统会先检查 `contest:{contestId}:exist` 是否存在。如果存在，就走 Redis 路径：先从 `contest:{contestId}:rank` 的 ZSET 中按分页范围取出用户 ID，再根据这些用户 ID 去取对应的 `contest:{contestId}:participant:{userId}:detail` 快照，最后组装成 `BoardListItemVO`。如果 `exist` 不存在，就走数据库路径，直接按 `contest_participant` 表的排序条件分页查询。两条路径返回的业务结果是一致的，差别只是一个走缓存，一个走数据库。数据库回退路径和 Redis 路径使用同一套排序规则：先按 `solvedCount` 降序，再按 `penalty` 升序，最后按 `lastAcceptedTime` 升序，所以即使缓存失效，榜单顺序也不会变，只是性能回到数据库侧。

这里还要注意，榜单分页不是单独缓存“第几页的榜单”，而是缓存整场比赛的排名序列和每个用户的详情。分页只是在查询时按 `start` / `end` 切片。这种设计比缓存分页结果更灵活，因为它可以支持任意页码、任意页大小，而不会因为一个比赛有多个分页版本就膨胀出很多缓存键。

`BoardPageVO` 里有一个 `problems` 字段，这个字段不是题目标题列表，而是按 `problemIndex` 排序的题目列映射，元素类型是 `ContestProblemDTO`，只保留 `problemId` 和 `problemIndex`。前端渲染榜单时，应该先根据这个列表确定列顺序，再按 `BoardListItemVO.problemResults` 中的 `problemId` 去取每题表现。换句话说，榜单页的“题目列”和“每个人的题目结果”是分开传的，前者负责列顺序，后者负责每题状态。

### 8.9 什么时候会重算整场榜单

整场榜单重算有两个入口。第一个入口是比赛结束后的定时任务 `refreshEndedContestBoards()`，它负责把封榜期内的提交正式并入结果，并写回 `board_refreshed_at`。第二个入口是管理员修改比赛开始时间、结束时间或封榜时间后，如果比赛已经开始，`ContestService.updateContest()` 会立即调用 `boardService.refreshContestBoard(contestId)`，重新计算当前可见快照，防止继续展示旧时间边界下的榜单。

`refreshContestBoard(contestId)` 的算法和单用户增量更新一样，都是按提交时间重放该比赛的全部非 Agent 提交，但它是针对整场比赛的所有参赛者逐个重算。它先取出所有参赛者，然后对每个用户加单用户锁，再按时间顺序重放其提交。比赛未结束时，它仍然会保留封榜视图；比赛结束后，它会把封榜后的有效提交也纳入正式结果。重算完数据库快照后，如果 `contest:{contestId}:exist` 仍然存在，就会同步刷新 Redis 的 rank 和 participant detail；如果缓存已经失效，则不会主动复活旧缓存。

## 9. 题目编排与比赛详情缓存

比赛题目编排和比赛详情缓存是两个不同层次的东西，不能混为一谈。`contest_problem:contest:{contestId}` 缓存的只是题目 ID 和题号标识，它的作用是让 `problem/queryInContest` 可以把比赛题号快速映射成真实题目 ID，也让比赛详情和榜单页可以迅速拿到题目编排。这个缓存 TTL 较长，当前默认是 5 小时，因为题目编排在比赛进行过程中通常不会频繁变化。

`contest_detail:contest:{contestId}` 则是聚合缓存，它会把比赛元数据和题目标题摘要一起缓存起来，TTL 比编排缓存短，当前默认 30 秒。这样做的原因是题目标题可能会变，而比赛详情页希望尽量显示最新标题，因此题目模块在更新题面后会通过 `ContestCacheService.evictContestDetailsByProblemId(problemId)` 反查所有引用该题目的比赛并删除详情缓存。编排缓存不需要跟着删，因为题目标题变化不会改变 `problemId` 和 `problemIndex` 的对应关系。

比赛分页公共缓存是第三层。`contest_page:current:{current}:size:{size}` 只缓存所有用户共享的比赛元数据，不缓存报名态和实时结束状态，因为这些字段是“用户态 + 当前时间态”的组合，不能放进公共缓存里。`ContestService.queryContestPage()` 会每次请求都补充这些字段，确保公共缓存保持纯净。

## 10. 请求校验与持久层职责

比赛请求参数集中由 [ContestRequestChecker](../contest/service/ContestRequestChecker.java) 校验。创建和更新比赛都要求 `name` 去首尾空格后不能为空且长度不超过 50；`startTime` 和 `endTime` 必填且必须满足 `startTime < endTime`；`freezeTime` 可以为空，非空时必须落在比赛时间区间内；`penaltyPerWrong` 可以不传，不传时后端会按 0 处理，显式传值则必须非负。更新比赛还会检查 `contestProblems`，要求每个题目 ID 和题号都合法、同一场比赛内不重复、题号长度不超过 10，而且列表里的题目必须真实存在。

持久层也拆得很细。`ContestQueryService` 负责读取比赛、分页查询比赛、扫描未来即将开始的比赛，以及扫描已经结束但还没刷新榜单的比赛。`ContestUpdateService` 负责插入比赛、更新比赛、清空 `board_refreshed_at`、以及在榜单解除封榜时标记刷新时间。`ContestProblemQueryService` 和 `ContestProblemUpdateService` 分别负责比赛题目编排的查询和全量覆盖写入。`ContestParticipantQueryService` 和 `ContestParticipantUpdateService` 则负责参赛者快照的查询、插入、删除和整行更新。这个拆法的核心目的就是把“单表职责”尽量压平，复杂编排放到 `ContestService` 和 `BoardService` 里做。

## 11. 几个必须记住的实现事实

这个模块里有一些实现事实，写文档时不能写成抽象愿景，必须按当前代码行为来描述。第一，比赛创建只建元数据，不建题目编排；第二，比赛更新是全量覆盖，不是 patch；第三，报名和取消报名都必须依赖当前登录态，不接收前端传来的用户 ID；第四，公开的比赛列表不会把报名态和实时结束状态写进缓存，都是请求时补出来的；第五，榜单计算忽略 Agent 提交，只处理 `isAgent = false` 的赛时提交；第六，封榜不是删数据，而是把可见和不可见的提交分开处理，比赛结束后再统一重算；第七，榜单缓存存在一个明确的活跃标记 `exist`，它比 ZSET 早十秒过期，是为了让查询路径有一个更稳定的缓存状态判断。

最后还有一条跨模块联动需要明确：`contest` 模块并不自己处理所有比赛相关的数据变化。题目标题更新会由 `problem` 模块反查并失效 `contest_detail`；提交评测完成会由 `submit` 模块发消息进入 `BoardService.handleJudgeResult()`；管理员修改题目编排会由 `ContestService.updateContest()` 负责同步失效 `contest_problem` 和 `contest_detail`。也就是说，比赛模块是编排中心，但不是孤立中心，它依赖其他模块提供准确的题目、提交和鉴权信息。

## 12. 文档用途

本文件用于说明比赛模块的元数据、编排、报名、详情、榜单以及缓存与定时任务的实现。更细的接口和类职责对应下列源码文件：

- [ContestController](../contest/controller/ContestController.java)
- [ContestService](../contest/service/ContestService.java)
- [BoardService](../contest/service/BoardService.java)
- [ContestCacheService](../contest/service/ContestCacheService.java)
- [ContestRequestChecker](../contest/service/ContestRequestChecker.java)
- [ContestParticipant](../contest/entity/ContestParticipant.java)
- [ContestParticipantQueryService](../contest/persistence/ContestParticipantQueryService.java)
- [ContestParticipantUpdateService](../contest/persistence/ContestParticipantUpdateService.java)
