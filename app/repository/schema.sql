CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(255) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    rating INTEGER DEFAULT 0,
    max_rating INTEGER DEFAULT 0,
    email VARCHAR(255) NOT NULL UNIQUE,
    bio TEXT,
    solved_count INTEGER DEFAULT 0
);


CREATE TABLE problem (
    id BIGSERIAL PRIMARY KEY,
    author_id BIGINT NOT NULL,
    visibility VARCHAR(32) NOT NULL,
    checker VARCHAR(32) NOT NULL,
    time_limit INTEGER NOT NULL,
    memory_limit INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    statement TEXT NOT NULL,
    solution TEXT,
    difficulty INTEGER,
    problem_key VARCHAR(255) NOT NULL,
    version INTEGER NOT NULL,
    test_case_num INTEGER NOT NULL
);

CREATE INDEX idx_problem_visibility_id ON problem (visibility, id ASC);
CREATE INDEX idx_problem_author_id_id ON problem (author_id, id ASC);


CREATE TABLE problem_tag (
    problem_id BIGINT NOT NULL,
    tag VARCHAR(64) NOT NULL,
    PRIMARY KEY (problem_id, tag)
);

CREATE INDEX problem_tag_tag_key ON problem_tag(tag);


CREATE TABLE submission (
    id BIGSERIAL PRIMARY KEY,
    type VARCHAR(32) NOT NULL,
    problem_id BIGINT,
    submitter_id BIGINT,
    is_agent BOOLEAN NOT NULL DEFAULT FALSE,
    submit_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    contest_id BIGINT,
    language VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    time_used_ms INTEGER,
    memory_used_mb INTEGER,
    info TEXT
);

CREATE INDEX idx_submission_contest_time ON submission (contest_id, submit_time DESC);
CREATE INDEX idx_submission_submitter_time ON submission (submitter_id, submit_time DESC);
CREATE INDEX idx_submission_contest_submitter_time ON submission (contest_id, submitter_id, submit_time DESC);


CREATE TABLE submission_detail (
    submission_id BIGINT NOT NULL,
    test_case_id INT NOT NULL,
    input TEXT,
    output TEXT,
    answer TEXT,
    info TEXT,
    status VARCHAR(32) NOT NULL,
    time_used_ms INTEGER,
    memory_used_mb INTEGER,
    PRIMARY KEY (submission_id, test_case_id)
);


CREATE TABLE contest (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    freeze_time TIMESTAMP,
    board_refreshed_at TIMESTAMP,
    penalty_per_wrong INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_contest_start_time_id ON contest (start_time DESC, id DESC);

CREATE TABLE contest_problem (
    contest_id BIGINT NOT NULL,
    problem_id BIGINT NOT NULL,
    problem_index VARCHAR(10) NOT NULL,

    CONSTRAINT uk_contest_problem_index UNIQUE (contest_id, problem_index),
    CONSTRAINT uk_contest_problem_id UNIQUE (contest_id, problem_id)
);

CREATE TABLE contest_participant (
    contest_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    username VARCHAR(255) NOT NULL,
    solved_count INTEGER NOT NULL DEFAULT 0,
    penalty INTEGER NOT NULL DEFAULT 0,
    last_accepted_time INTEGER NOT NULL DEFAULT 0,
    problem_results JSONB NOT NULL DEFAULT '{}'::jsonb,

    PRIMARY KEY (contest_id, user_id)
);

CREATE INDEX idx_user_contest ON contest_participant (user_id, contest_id);
CREATE INDEX idx_contest_participant_contest_solved_penalty_time ON contest_participant (contest_id, solved_count DESC, penalty ASC, last_accepted_time ASC);
