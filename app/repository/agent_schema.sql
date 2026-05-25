CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    title VARCHAR(255),
    updated_at TIMESTAMP NOT NULL,
    current_turn INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,

    CHECK (current_turn >= 1),
    CHECK (status IN ('finished', 'running'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations (user_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS messages (
    conversation_id UUID NOT NULL,
    turn_index INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,

    CHECK (turn_index >= 1),
    CHECK (role IN ('user', 'agent')),
    CHECK (jsonb_typeof(attachments) = 'array'),

    PRIMARY KEY (conversation_id, turn_index, role)
);


CREATE TABLE IF NOT EXISTS states (
    id UUID PRIMARY KEY,
    state JSONB NOT NULL
);


CREATE TABLE IF NOT EXISTS tasks (
    conversation_id UUID NOT NULL,
    turn_index INTEGER NOT NULL,
    task_id UUID NOT NULL,
    state UUID,

    CHECK (turn_index >= 1),

    PRIMARY KEY (conversation_id, turn_index),
    UNIQUE (task_id)
);
