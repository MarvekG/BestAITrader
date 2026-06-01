#!/usr/bin/env python3
"""
Backfill historical debate messages into the memory service.

Examples:
  python backend/scripts/backfill_debate_memory.py \
    --database-url postgresql://postgres:password@127.0.0.1:5432/trading \
    --memory-base-url http://127.0.0.1:8010

  python backend/scripts/backfill_debate_memory.py --session-id <uuid> --dry-run

  python backend/scripts/backfill_debate_memory.py \
    --session-id <uuid> \
    --fixed-memory-user-id 900001
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical debate records into the memory service.")
    parser.add_argument("--database-url", help="Override backend DATABASE_URL for this script run.")
    parser.add_argument("--memory-base-url", help="Override MEMORY_SERVICE_BASE_URL for this script run.")
    parser.add_argument("--session-id", help="Only backfill one session.")
    parser.add_argument("--stock-code", help="Only backfill one stock code.")
    parser.add_argument("--user-id", type=int, help="Only backfill one user.")
    parser.add_argument("--limit", type=int, help="Maximum number of debate messages to process.")
    parser.add_argument(
        "--memory-timeout-seconds",
        type=float,
        default=300.0,
        help="Timeout to use for each memory service request during backfill.",
    )
    parser.add_argument(
        "--fixed-memory-user-id",
        type=int,
        help=(
            "Write all imported memories into one fixed testing user space "
            "(user:<id>:general) instead of each row's original user/stock scope."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan and print candidates without writing to memory.")
    return parser.parse_args()


args = parse_args()

from app.core.config import settings

if args.database_url:
    settings.DATABASE_URL = args.database_url
if args.memory_base_url:
    settings.MEMORY_SERVICE_BASE_URL = args.memory_base_url
    settings.MEMORY_SERVICE_ENABLED = True
settings.MEMORY_SERVICE_TIMEOUT_SECONDS = max(1.0, float(args.memory_timeout_seconds))

from app.core.database import SessionLocal
from app.ai.memory_client import memory_client
from app.models.debate_message import DebateMessage
from app.models.session import Session as SessionModel


def _build_debate_content(
    *,
    debate_msg: DebateMessage,
    session_obj: SessionModel,
) -> str:
    stock_code = str(session_obj.stock_code or "").strip()
    content_parts = [
        f"Historical debate record for {stock_code}.",
        f"Session: {debate_msg.session_id}",
        f"Stage: {debate_msg.stage}",
        f"Round: {debate_msg.round_number}",
        f"Agent: {debate_msg.agent_name} ({debate_msg.agent_role})",
    ]
    if debate_msg.decision:
        content_parts.append(f"Decision: {debate_msg.decision}")
    if debate_msg.confidence is not None:
        content_parts.append(f"Confidence: {debate_msg.confidence:.4f}")

    reasoning_text = str(debate_msg.reasoning or "").strip()
    if reasoning_text:
        content_parts.append("")
        content_parts.append("Reasoning:")
        content_parts.append(reasoning_text[:14000])

    analysis = debate_msg.analysis if isinstance(debate_msg.analysis, dict) else None
    if analysis:
        import json

        content_parts.append("")
        content_parts.append("Structured analysis:")
        content_parts.append(json.dumps(analysis, ensure_ascii=False, sort_keys=True)[:4000])

    return "\n".join(content_parts).strip()


def build_query(db):
    query = (
        db.query(DebateMessage, SessionModel)
        .join(SessionModel, DebateMessage.session_id == SessionModel.session_id)
        .filter(SessionModel.user_id.isnot(None), SessionModel.stock_code.isnot(None))
        .order_by(DebateMessage.created_at.asc(), DebateMessage.message_id.asc())
    )
    if args.session_id:
        query = query.filter(SessionModel.session_id == args.session_id)
    if args.stock_code:
        query = query.filter(SessionModel.stock_code == args.stock_code)
    if args.user_id is not None:
        query = query.filter(SessionModel.user_id == args.user_id)
    if args.limit:
        query = query.limit(max(1, args.limit))
    return query


async def backfill() -> int:
    if not memory_client.enabled and not args.dry_run:
        print("Memory service is not enabled. Set --memory-base-url or MEMORY_SERVICE_BASE_URL first.")
        return 1

    db = SessionLocal()
    try:
        rows = list(build_query(db).all())
        print(f"Matched debate messages: {len(rows)}")
        if args.dry_run:
            for debate_msg, session_obj in rows[:10]:
                print(
                    f"[DRY-RUN] message_id={debate_msg.message_id} session_id={debate_msg.session_id} "
                    f"stock_code={session_obj.stock_code} user_id={session_obj.user_id} "
                    f"agent_role={debate_msg.agent_role} stage={debate_msg.stage} round={debate_msg.round_number}"
                )
            return 0

        succeeded = 0
        failed = 0
        fixed_user_space = args.fixed_memory_user_id is not None
        if fixed_user_space:
            print(f"Destination testing user space: user:{args.fixed_memory_user_id}:general")
        for debate_msg, session_obj in rows:
            destination_user_id = args.fixed_memory_user_id or session_obj.user_id
            content = _build_debate_content(
                debate_msg=debate_msg,
                session_obj=session_obj,
            )
            response = await memory_client.write_memory(
                user_id=destination_user_id,
                stock_code=None if fixed_user_space else session_obj.stock_code,
                content=content,
            )
            last_error = memory_client.get_last_error("ingest")
            if last_error:
                failed += 1
                print(
                    f"[FAILED] message_id={debate_msg.message_id} session_id={debate_msg.session_id} "
                    f"error={last_error.get('message') or 'unknown'}"
                )
                continue
            succeeded += 1
            print(
                f"[OK] message_id={debate_msg.message_id} event_id={response.get('event_id')} "
                f"status={response.get('status')} destination_user_id={destination_user_id} "
                f"scope={'user:'+str(destination_user_id)+':general' if fixed_user_space else 'origin-user-scope'}"
            )

        print(f"Backfill completed: succeeded={succeeded}, failed={failed}, total={len(rows)}")
        return 0 if failed == 0 else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(backfill()))
