-- Rebuild chat-style Deep Research stock picker schema.
-- Target: docker exec -i best_ai_trader_postgres psql -U tradeuser -d trading
-- This intentionally removes the previous interactive research task/evidence/candidate/finding/artifact tables.

BEGIN;

DROP SCHEMA IF EXISTS stock_picker_interactive CASCADE;
CREATE SCHEMA stock_picker_interactive;

CREATE TABLE stock_picker_interactive.research_runs (
    run_id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id),
    status VARCHAR(50) NOT NULL DEFAULT 'drafting_plan',
    current_stage VARCHAR(50) NOT NULL DEFAULT 'drafting_plan',
    current_phase VARCHAR(30) NOT NULL DEFAULT 'planning',
    title VARCHAR(160) NOT NULL,
    raw_requirement TEXT NOT NULL,
    pending_message_id UUID,
    checkpoint_payload JSON NOT NULL DEFAULT '{}'::json,
    cache_context_version VARCHAR(80) NOT NULL DEFAULT 'research-agent-v1',
    version INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    CONSTRAINT ck_interactive_research_runs_status CHECK (
        status IN (
            'awaiting_plan_approval',
            'awaiting_user_input',
            'cancelled',
            'completed',
            'drafting_plan',
            'failed',
            'reflecting',
            'researching',
            'synthesizing'
        )
    ),
    CONSTRAINT ck_interactive_research_runs_phase CHECK (
        current_phase IN ('planning', 'reflection', 'research', 'synthesis')
    )
);

CREATE INDEX ix_interactive_research_runs_user_id
    ON stock_picker_interactive.research_runs (user_id);
CREATE INDEX ix_interactive_research_runs_status
    ON stock_picker_interactive.research_runs (status);
CREATE INDEX ix_interactive_research_runs_current_phase
    ON stock_picker_interactive.research_runs (current_phase);
CREATE INDEX ix_interactive_research_runs_pending_message_id
    ON stock_picker_interactive.research_runs (pending_message_id);
CREATE INDEX ix_interactive_research_runs_user_created_at
    ON stock_picker_interactive.research_runs (user_id, created_at);
CREATE INDEX ix_interactive_research_runs_status_created_at
    ON stock_picker_interactive.research_runs (status, created_at);

CREATE TABLE stock_picker_interactive.research_messages (
    message_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES stock_picker_interactive.research_runs(run_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    message_type VARCHAR(40) NOT NULL,
    content TEXT NOT NULL,
    payload JSON NOT NULL DEFAULT '{}'::json,
    parent_message_id UUID,
    sequence_no INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    visible_to_user BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_interactive_messages_role CHECK (
        role IN ('assistant', 'system', 'tool', 'user')
    ),
    CONSTRAINT ck_interactive_messages_type CHECK (
        message_type IN (
            'assistant_question',
            'assistant_text',
            'final_result',
            'plan_card',
            'progress_update',
            'system_status',
            'tool_result',
            'tool_start',
            'user_input'
        )
    ),
    CONSTRAINT ck_interactive_messages_status CHECK (
        status IN ('completed', 'created', 'failed', 'queued', 'streaming')
    ),
    CONSTRAINT uq_interactive_messages_run_sequence UNIQUE (run_id, sequence_no)
);

CREATE INDEX ix_interactive_messages_run_id
    ON stock_picker_interactive.research_messages (run_id);
CREATE INDEX ix_interactive_messages_message_type
    ON stock_picker_interactive.research_messages (message_type);
CREATE INDEX ix_interactive_messages_parent_message_id
    ON stock_picker_interactive.research_messages (parent_message_id);
CREATE INDEX ix_interactive_messages_run_sequence
    ON stock_picker_interactive.research_messages (run_id, sequence_no);
CREATE INDEX ix_interactive_messages_run_created_at
    ON stock_picker_interactive.research_messages (run_id, created_at);

COMMIT;
