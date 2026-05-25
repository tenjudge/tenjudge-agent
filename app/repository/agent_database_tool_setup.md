# 智能体数据库工具配置

1. 执行 `agent_tool_views.sql`。
2. 给 `tenjudge_agent_tool` 设置登录密码：

```sql
ALTER ROLE tenjudge_agent_tool WITH PASSWORD '你的密码';
```

3. 在 `.env` 中配置：

```env
AGENT_DB_TOOL_DATABASE_URL=postgresql://tenjudge_agent_tool:密码@地址:端口/数据库
```

4. `DATABASE_URL` 继续作为主业务连接使用。
