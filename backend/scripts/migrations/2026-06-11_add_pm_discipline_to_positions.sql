-- Migration: add PM discipline columns to positions
-- Usage: docker exec -i best_ai_trader_postgres psql -U tradeuser -d trading < backend/scripts/migrations/2026-06-11_add_pm_discipline_to_positions.sql
ALTER TABLE positions ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(10, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS take_profit NUMERIC(10, 4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS horizon_deadline TIMESTAMP;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS pm_session_id UUID;
