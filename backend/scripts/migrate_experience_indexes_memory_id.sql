BEGIN;

ALTER TABLE experience_indexes
    DROP CONSTRAINT IF EXISTS uq_experience_indexes_user_memory_observation;

ALTER TABLE experience_indexes
    DROP CONSTRAINT IF EXISTS uq_experience_indexes_user_memory_id;

DELETE FROM experience_indexes
WHERE memory_observation_id IS NULL;

ALTER TABLE experience_indexes
    RENAME COLUMN memory_observation_id TO memory_id;

ALTER TABLE experience_indexes
    DROP COLUMN memory_source_id;

ALTER TABLE experience_indexes
    ALTER COLUMN memory_id SET NOT NULL;

ALTER TABLE experience_indexes
    ADD CONSTRAINT uq_experience_indexes_user_memory_id UNIQUE (user_id, memory_id);

COMMIT;
