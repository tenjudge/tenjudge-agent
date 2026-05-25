CREATE SCHEMA IF NOT EXISTS agent_read;

REVOKE ALL ON SCHEMA agent_read FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA agent_read FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA agent_read FROM PUBLIC;

-- Set the login password outside this file, for example:
-- ALTER ROLE tenjudge_agent_tool PASSWORD 'replace-with-secret';


CREATE OR REPLACE VIEW agent_read.problem
WITH (security_barrier = true) AS
SELECT
    id,
    author_id,
    visibility,
    checker,
    time_limit,
    memory_limit,
    name,
    statement,
    solution,
    difficulty,
    version,
    test_case_num
FROM public.problem
WHERE visibility = 'public';


CREATE OR REPLACE VIEW agent_read.problem_tag
WITH (security_barrier = true) AS
SELECT
    pt.problem_id,
    pt.tag
FROM public.problem_tag pt
JOIN public.problem p ON p.id = pt.problem_id
WHERE p.visibility = 'public';


CREATE OR REPLACE VIEW agent_read.users
WITH (security_barrier = true) AS
SELECT
    id,
    username,
    created_at,
    role,
    rating,
    max_rating,
    bio,
    solved_count
FROM public.users;


CREATE OR REPLACE VIEW agent_read.contest
WITH (security_barrier = true) AS
SELECT
    id,
    name,
    start_time,
    end_time,
    freeze_time,
    board_refreshed_at,
    penalty_per_wrong
FROM public.contest;


CREATE OR REPLACE VIEW agent_read.contest_problem
WITH (security_barrier = true) AS
SELECT
    cp.contest_id,
    cp.problem_id,
    cp.problem_index,
    p.name AS problem_name,
    p.visibility AS problem_visibility
FROM public.contest_problem cp
JOIN public.problem p ON p.id = cp.problem_id;


CREATE OR REPLACE VIEW agent_read.contest_participant
WITH (security_barrier = true) AS
SELECT
    contest_id,
    user_id,
    username,
    solved_count,
    penalty,
    last_accepted_time,
    problem_results
FROM public.contest_participant;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'tenjudge_agent_tool'
    ) THEN
        CREATE ROLE tenjudge_agent_tool LOGIN;
    END IF;
END
$$;

ALTER ROLE tenjudge_agent_tool SET search_path = agent_read;
ALTER ROLE tenjudge_agent_tool SET default_transaction_read_only = on;
ALTER ROLE tenjudge_agent_tool SET statement_timeout = '3s';

REVOKE ALL ON SCHEMA public FROM tenjudge_agent_tool;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM tenjudge_agent_tool;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM tenjudge_agent_tool;

GRANT USAGE ON SCHEMA agent_read TO tenjudge_agent_tool;
GRANT SELECT ON ALL TABLES IN SCHEMA agent_read TO tenjudge_agent_tool;

ALTER DEFAULT PRIVILEGES IN SCHEMA agent_read
    GRANT SELECT ON TABLES TO tenjudge_agent_tool;
