# Auth 鉴权模块

`auth` 模块是 TenJudge 服务端的统一身份入口，负责登录态管理、注册、登出、角色判定、公开用户查询以及管理员角色修改。它并不是一个只做“登录页”的轻量模块，而是整个后端权限体系的根部：题目能不能看、能不能提交，比赛能不能管理，某个接口能不能被匿名访问，最终都要回到这里的登录态和角色判断上。

本文档按“模块职责 -> 对外接口 -> 数据模型 -> 权限与缓存 -> 实现链路 -> 初始化流程 -> 关键约束”的顺序整理，尽量把实现逻辑串成完整叙述。

相关代码入口如下：

- [AuthController](../auth/controller/AuthController.java)
- [AuthService](../auth/service/AuthService.java)
- [AuthChecker](../auth/service/AuthChecker.java)
- [AuthRequestChecker](../auth/service/AuthRequestChecker.java)
- [StpService](../auth/service/StpService.java)
- [UserQueryService](../auth/persistence/UserQueryService.java)
- [UserUpdateService](../auth/persistence/UserUpdateService.java)
- [AuthRunner](../auth/runner/AuthRunner.java)
- [Validator](../auth/utils/Validator.java)
- [Converter](../auth/utils/Converter.java)

## 1. 模块职责

这个模块的核心任务是把“当前请求是谁”以及“这个人能做什么”稳定下来，并且把这种判断收束成一套后端统一逻辑，而不是散落在各个 Controller 里。登录和注册只是入口，真正重要的是后续业务链路都可以通过统一的 `AuthService` 和 `AuthChecker` 去确认当前用户状态，减少重复实现和权限口径不一致的问题。

从职责上看，`auth` 模块负责四件事。第一是认证，也就是登录、退出和登录态查询；第二是身份注册，也就是普通用户注册以及超级管理员代注册管理员或超级管理员；第三是角色治理，包括 `user`、`admin`、`super_admin` 三种角色的读取、缓存和修改；第四是用户信息公开查询，包括对外展示的用户资料和当前 token 对应的轻量用户 ID 查询。除此之外，这个模块还负责在应用启动时初始化超级管理员账号，保证系统在空库状态下也能被正确接管。

## 2. 对外接口

`AuthController` 暴露的接口都挂在 `/auth` 下，接口语义比较明确：注册、登录、登出、查看当前用户、查看当前 token 对应的用户 ID、公开查询用户，以及超级管理员修改用户角色。这里的返回体统一包在 `Result<T>` 中，接口成功时返回 `Result.success(...)`，失败时由全局异常处理器统一接管，不会在 Controller 中手工拼错误格式。

`POST /auth/register` 用于注册用户，输入是 `RegisterRequest`。这个接口允许普通用户直接注册 `role=user`，但如果请求里要创建 `admin` 或 `super_admin`，则必须先具备超级管理员身份，否则会在后端权限检查阶段被拒绝。注册成功后只返回新用户 ID，不返回密码等敏感信息。

`POST /auth/login` 用于登录，输入是 `LoginRequest`，`account` 可以是用户名，也可以是邮箱。这个接口成功后返回 `LoginVO`，其中包含登录请求后续需要携带的 `tokenName` 和 `tokenValue`，还会附带一份当前用户的 `UserVO`。这意味着前端不需要在登录后再额外请求一次用户详情，登录响应本身已经携带了基础用户信息。

`DELETE /auth/logout` 用于登出当前 token 对应的登录态，接口本身只要带上当前有效 token 即可，不需要额外参数。`GET /auth/me` 用于查询当前登录用户的完整信息，只有登录态合法时才返回 `UserVO`，否则会按未授权处理。`GET /auth/me/id` 则是一个更轻量的接口，它只返回当前 token 对应的用户 ID；如果未登录，这个接口不会抛业务错误，而是返回 `data.userId = null`，方便前端做登录态探测。它本身只看登录态，不会再额外校验用户记录是否仍然存在。

`GET /auth/user` 是公开用户查询接口，不要求登录，但要求查询条件必须且只能传一个，也就是只能传 `userId` 或 `username` 其中之一，不能同时传，也不能都不传。这个接口返回的依然是 `UserVO`，只是会把 `email` 字段强制置空，避免匿名用户直接拿到邮箱这种登录凭据相关信息。

`PUT /auth/admin/user/role` 是超级管理员修改用户角色的接口，输入是 `UserRoleUpdateRequest`。这个接口是高危操作，只能由超级管理员调用，而且还额外禁止超级管理员修改自己的角色，防止权限自锁。角色修改成功后会立即失效 `user:role:{userId}` 缓存，避免后续权限检查继续读到旧角色。

## 3. 用户角色与数据模型

项目中的角色统一小写存储，只有三种合法值：`super_admin`、`admin` 和 `user`。其中 `super_admin` 拥有最高权限，可以管理用户、题目和比赛；`admin` 主要承担题目管理能力；`user` 是普通用户，没有额外的管理权限。这个角色体系不仅用于接口授权，也用于题目查看、题目提交、比赛管理等后续模块的权限判定，所以它不是一个纯展示字段，而是整个系统的核心控制面之一。

底层数据库对应的是 `users` 表，实体类是 [User](../auth/entity/User.java)。表里保存了用户 ID、用户名、密码哈希、创建时间、角色、评分、最高评分、邮箱、简介以及已解决题目数。`UserVO` 则是对外返回的用户资料对象，字段和实体相近，但它不是实体直出，而是通过 [Converter](../auth/utils/Converter.java) 转换后再返回给前端。这样做的目的很直接：一方面把数据结构和返回结构隔离开，另一方面避免把密码哈希这类敏感字段暴露出去。

在返回侧，`LoginVO` 包含 token 名称、token 值以及当前登录用户信息；`RegisterVO` 只返回新建用户 ID；`CurrentUserIdVO` 只返回轻量的 `userId`；`UserVO` 则承载完整的公开用户信息，包括 `username`、`createdAt`、`role`、`rating`、`maxRating`、`email`、`bio` 和 `solvedCount`。这里需要特别注意，`UserVO` 在不同接口里的含义并不完全一样：登录和当前用户查询可以返回完整 `email`，公开用户查询会强制把 `email` 置空，这是一个明确的安全边界，不是前端自行处理的约定。

## 4. 注册、登录和登出

注册逻辑的入口在 [AuthService.register()](../auth/service/AuthService.java)。它首先通过 `AuthRequestChecker` 校验请求本身是否合法，再根据角色决定是否需要更高权限。普通用户注册只要求字段合法并且用户名、邮箱唯一；如果注册的是 `admin` 或 `super_admin`，则必须先通过超级管理员校验。注册时密码不会以明文落库，而是先使用 `BCrypt` 生成哈希，再交给 `UserUpdateService` 插入数据库。

注册流程里有一个比较重要的设计点：校验唯一性是前置做的，不是先插库再靠数据库唯一约束兜底。也就是说，用户名和邮箱如果已经存在，会尽早抛出明确的业务错误，而不是让请求走到数据库层再炸出一个难以归类的异常。这个设计让前端和调用方更容易得到确定反馈，也减少了无意义的插库尝试。

登录入口同样在 `AuthService` 内部。`login()` 会先判断 `account` 和 `password` 是否为空，然后根据 `account` 是否包含 `@` 来区分用户名登录还是邮箱登录。查到用户后，使用 `BCrypt.checkpw()` 校验密码；密码正确时调用 `StpService.login(userId)` 建立登录态，再把 `tokenName`、`tokenValue` 和当前用户信息一起返回。这里的一个细节是，登录失败不会细分成“账号不存在”“密码错误”之类的不同响应，而是统一返回 `LOGIN_FAILED`，这是刻意设计的对外口径，避免泄露账号是否存在。

登出逻辑则非常直接，`logout()` 只是转调 `StpService.logout()`，把当前 token 对应的登录态注销掉。整个模块没有再单独维护一套自定义 session 结构，而是把登录态统一收束到 Sa-Token，业务代码通过 `StpService` 访问它。这种封装的意义是后续单元测试可以 mock `StpService`，而不用直接碰 `StpUtil` 的静态调用。

## 5. 当前登录用户与公开用户查询

`GET /auth/me` 和 `GET /auth/me/id` 看起来都和“当前用户”有关，但它们的语义差别很大。`me()` 是完整身份查询，调用 `authChecker.checkLogin()` 后再到数据库里按 `userId` 取出用户实体并转换成 `UserVO`。如果登录态不存在，或者 token 对应的用户记录已经被删除，它会返回业务错误或者用户不存在错误，因为这个接口的前提就是“我已经登录且仍然有效”。相反，`getCurrentUserId()` 是一个轻量探测接口，它只关心当前 token 是否还能解析出用户 ID；如果没有登录或者 token 已失效，它不会报错，而是直接返回空的 `CurrentUserIdVO`，让前端把它视作匿名态。

公开用户查询接口 `GET /auth/user` 则是为了让匿名访问者或者其他模块按用户名或 ID 查看公开资料。这个接口在参数层面有严格约束：`userId` 和 `username` 必须二选一，不能模糊搜索，也不能两个都传。`AuthRequestChecker.checkPublicUserQuery()` 会先把这个条件掐死，然后再按指定条件查用户。查到以后会通过 `Converter.toUserVO()` 组装返回值，但会把 `email` 字段清空。这里的设计思路是公开资料可以展示角色、评分、简介等信息，但邮箱属于登录凭据相关字段，不能直接公开。

## 6. 权限判断与角色缓存

`AuthChecker` 是这个模块真正的权限核心。它封装了三个最常用的判断：是否登录、是否管理员、是否超级管理员。所有这些判断都先依赖 `StpService` 确认 token 状态，再依赖 `getRole()` 读取角色。读取角色时并不会每次都打数据库，而是先查 Redis 中的 `user:role:{userId}`，如果缓存不存在再去数据库查 `User.role`，查到后再写回 Redis，TTL 名称使用 `user-role`。这样做的目的很清楚：角色是高频读取的数据，而登录态下的权限校验会在多个模块里重复发生，缓存能显著减少数据库压力。

角色缓存的失效只在角色真正变化时发生。最典型的场景是 `PUT /auth/admin/user/role`，在超级管理员把某个用户的角色修改成功以后，系统会立刻删除 `user:role:{userId}`，确保后续 `checkAdmin()`、`checkSuperAdmin()` 和其他角色读取逻辑都能看到新值。这个失效动作不是可选项，而是角色变更链路的一部分，因为如果缓存没清掉，权限检查会继续使用旧角色，直接导致越权或误拒绝。

`checkLogin()`、`checkAdmin()` 和 `checkSuperAdmin()` 的层级关系也很明确。`checkLogin()` 只负责确认登录态并返回用户 ID；`checkAdmin()` 会在登录的基础上再判断角色是不是 `admin` 或 `super_admin`；`checkSuperAdmin()` 则进一步要求角色必须是 `super_admin`。这些方法既可以给 Controller 用，也可以给其他业务模块用，比如题目模块和比赛模块都依赖它们做管理端校验。

## 7. 请求校验与业务约束

`AuthRequestChecker` 负责把请求里的基础约束先过滤掉，不让不合法的数据进入后面的业务分支。注册请求会检查用户名、密码、邮箱和角色是否满足格式要求，同时还会在写入前检查用户名和邮箱是否已存在。用户名要求以字母开头，长度为 3 到 20 个字符，只允许字母、数字和下划线；密码长度要求 8 到 20 个字符；邮箱要符合标准邮箱格式；角色只能是三个预定义值之一。这些规则都集中在 [Validator](../auth/utils/Validator.java) 中，避免正则表达式或字符串集合散落在业务代码里。

公开用户查询和角色更新也有各自的请求级校验。公开用户查询要求 `userId` 和 `username` 二选一，这不是参数风格偏好，而是接口语义约束，因为这个接口不应该被扩展成模糊搜索入口。角色更新则要求 `userId` 必须有效、`role` 必须合法。业务上任何可预期失败都不会在这里返回布尔值，而是直接抛 `BizException`，并由全局异常处理统一转成 `Result`。

## 8. 超级管理员初始化

`AuthRunner` 是模块启动阶段的重要补充逻辑。应用启动后，它会读取 `app.auth.super-admin-username`、`app.auth.super-admin-password` 和 `app.auth.super-admin-email`，然后检查数据库里是否已经存在同名超级管理员。如果同名用户已经存在，就直接跳过初始化；如果用户名不存在但邮箱已经被别的用户占用，则直接抛出运行时异常，因为这说明初始化配置和现有库状态冲突，不能静默继续。

如果超级管理员不存在，`AuthRunner` 会把配置中的密码先做 `BCrypt` 哈希，再构造一个 `RegisterRequest` 风格的数据写入用户表，但角色固定为 `super_admin`。这个流程保证系统在首次部署时就能自动生成接管账号，同时又不会因为多次启动而重复插入。需要注意的是，这里是启动时的系统级初始化，不是普通注册接口，因此它直接调用 `UserUpdateService.insert()`，也不会走前端路径。

## 9. 代码组织方式

`AuthService` 是对外的统一业务入口，Controller 和其他模块都应该优先调用它，而不是直接碰数据库或静态工具。`AuthChecker` 负责权限判断和角色读取，`AuthRequestChecker` 负责请求本身是否合法，`UserQueryService` 和 `UserUpdateService` 则是面向表级操作的 persistence 层。`StpService` 只是对 Sa-Token 的轻包装，它存在的意义是把静态调用变成可注入 Bean，便于单元测试和业务统一接入。`Converter` 和 `Validator` 分别负责对象转换和请求合法性判断，属于纯工具类，不承载业务状态。

这种分层方式有一个明显好处：登录、注册、角色查询和用户信息展示虽然看起来是几个不同接口，但它们共享同一套角色读取、输入校验和实体转换逻辑，不会在 Controller 里出现重复的用户名规则、邮箱规则或角色判断。后续如果要修改某个约束，通常只需要改 `Validator`、`AuthRequestChecker` 或 `AuthChecker` 中的单一位置，调用方不需要跟着复制改动。

## 10. 错误码与异常语义

这个模块里最常见的业务失败包括用户名非法、密码非法、邮箱非法、角色非法、用户名或邮箱已存在、未登录、无权限、用户不存在、注册失败、登录失败，以及公开用户查询参数不合法。所有这些失败都应该通过 `BizException` 抛出，再由全局异常处理器统一转换成 `Result`。这里的一个重要约束是：`BizException` 的 message 使用英文，普通运行时异常和日志消息使用中文，这样业务码稳定，而日志更容易排障。

## 11. 这个模块与其他模块的关系

`auth` 模块不是独立孤岛，它直接影响 `problem`、`contest` 和 `submit`。题目查看里的管理员判定、题目可见性修改、比赛管理的权限控制、提交链路里“能不能提交”以及“是不是本人操作”，都依赖这里的登录态和角色缓存。尤其是 `user:role:{userId}` 这个缓存，虽然看起来只是一个用户属性，但它实际上是整个系统多个高频权限检查的共同入口，所以它的失效时机必须和角色变更链路绑定，而不是单独靠 TTL 自然过期。

文档末尾列出的源码文件对应各个接口和类的具体实现：

- [AuthController](../auth/controller/AuthController.java)
- [AuthService](../auth/service/AuthService.java)
- [AuthChecker](../auth/service/AuthChecker.java)
- [AuthRequestChecker](../auth/service/AuthRequestChecker.java)
- [AuthRunner](../auth/runner/AuthRunner.java)
- [UserQueryService](../auth/persistence/UserQueryService.java)
- [UserUpdateService](../auth/persistence/UserUpdateService.java)
