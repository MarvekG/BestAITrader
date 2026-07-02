from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update

from app.core import database as database_module
from app.crud.base import CRUDBase
from app.models.account import Account
from app.models.async_task import AsyncTask
from app.models.data_storage import StockBasic
from app.models.debate_message import DebateMessage
from app.models.order import Order
from app.models.session import Session
from app.models.position import Position
from app.models.trade_record import TradeRecord
from app.models.user import User
from app.schemas.session import SessionCreate, SessionUpdate


class CRUDSession(CRUDBase[Session, SessionCreate, SessionUpdate]):
    async def _populate_metadata(self, db, sessions: Iterable[Session]) -> None:
        """补齐会话响应需要的股票名称和结束时间。"""
        session_list = list(sessions)
        if not session_list:
            return

        stock_codes = [session.stock_code for session in session_list]
        basics_result = await db.execute(
            select(StockBasic.stock_code, StockBasic.name).where(StockBasic.stock_code.in_(stock_codes))
        )
        name_map = {code: name for code, name in basics_result.all()}
        session_ids = {str(session.session_id) for session in session_list}
        ended_at_map = {}

        async_task_result = await db.execute(
            select(AsyncTask).where(
                AsyncTask.task_type == "ai_analysis",
                AsyncTask.completed_at.isnot(None),
            )
        )
        for task in async_task_result.scalars().all():
            parameters = task.parameters if isinstance(task.parameters, dict) else {}
            task_session_id = parameters.get("session_id")
            if task_session_id not in session_ids or task.completed_at is None:
                continue
            current_ended_at = ended_at_map.get(task_session_id)
            if current_ended_at is None or task.completed_at > current_ended_at:
                ended_at_map[task_session_id] = task.completed_at

        debate_result = await db.execute(
            select(DebateMessage.session_id, DebateMessage.created_at).where(
                DebateMessage.session_id.in_([session.session_id for session in session_list])
            )
        )
        for session_id, created_at in debate_result.all():
            session_id_str = str(session_id)
            if session_id_str in ended_at_map:
                continue
            current_ended_at = ended_at_map.get(session_id_str)
            if current_ended_at is None or created_at > current_ended_at:
                ended_at_map[session_id_str] = created_at

        for session in session_list:
            setattr(session, "stock_name", name_map.get(session.stock_code, session.stock_code))
            setattr(session, "ended_at", ended_at_map.get(str(session.session_id)))

    async def create(self, *, obj_in: SessionCreate) -> Session:
        """异步创建分析会话并初始化关联账户/持仓。"""
        async with database_module.AsyncSessionLocal() as db:
            session = Session(**obj_in.model_dump())
            db.add(session)
            await db.flush()

            account = None
            if session.user_id:
                account_result = await db.execute(select(Account).where(Account.user_id == session.user_id))
                account = account_result.scalar_one_or_none()
                if account is None:
                    account = Account(
                        user_id=session.user_id,
                        total_assets=Decimal("100000.00"),
                        initial_capital=Decimal("100000.00"),
                        available_cash=Decimal("100000.00"),
                        frozen_cash=Decimal("0.00"),
                        market_value=Decimal("0.00"),
                        total_profit_loss=Decimal("0.00"),
                        profit_loss_pct=Decimal("0.00"),
                        total_trades=0,
                        win_rate=Decimal("0.00"),
                    )
                    db.add(account)
                    await db.flush()

            if account:
                existing_position_result = await db.execute(
                    select(Position).where(
                        Position.account_id == account.account_id,
                        Position.stock_code == session.stock_code,
                    )
                )
                existing_position = existing_position_result.scalar_one_or_none()
                if existing_position:
                    if not existing_position.session_id:
                        existing_position.session_id = session.session_id
                else:
                    db.add(Position(
                        account_id=account.account_id,
                        session_id=session.session_id,
                        stock_code=session.stock_code,
                        total_shares=0,
                        available_shares=0,
                        frozen_shares=0,
                        avg_cost=0.0,
                        current_price=0.0,
                        market_value=0.0,
                        profit_loss=0.0,
                        profit_loss_pct=0.0,
                        purchase_details={},
                    ))

            await db.commit()
            await db.refresh(session)
            await self._populate_metadata(db, [session])
            return session

    async def get_owned(self, *, session_id: UUID, user_id: int) -> Optional[Session]:
        """异步读取用户拥有的分析会话。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.session_id == session_id,
                    Session.user_id == user_id,
                )
            )
            session = result.scalar_one_or_none()
            if session:
                await self._populate_metadata(db, [session])
            return session

    async def list_for_user(
        self,
        *,
        user_id: int,
        skip: int,
        limit: int,
        status: str | None = None,
        source: str | None = None,
        q: str | None = None,
    ) -> tuple[list[Session], int]:
        """异步查询用户会话列表。"""
        async with database_module.AsyncSessionLocal() as db:
            stmt = select(Session).where(Session.user_id == user_id)
            if status:
                stmt = stmt.where(Session.status == status)
            if source:
                stmt = stmt.where(Session.source == source)
            if q:
                search = f"%{q.strip()}%"
                stmt = stmt.outerjoin(StockBasic, StockBasic.stock_code == Session.stock_code)
                stmt = stmt.where(
                    or_(
                        Session.stock_code.ilike(search),
                        StockBasic.name.ilike(search),
                    )
                )

            total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = total_result.scalar_one()
            result = await db.execute(stmt.order_by(Session.created_at.desc()).offset(skip).limit(limit))
            sessions = result.scalars().all()
            await self._populate_metadata(db, sessions)
            return list(sessions), total

    async def update(self, *, session_id: UUID, user_id: int, obj_in: SessionUpdate) -> Optional[Session]:
        """异步更新用户拥有的分析会话。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.session_id == session_id,
                    Session.user_id == user_id,
                )
            )
            session = result.scalar_one_or_none()
            if session is None:
                return None
            update_data = obj_in.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(session, field, value)
            await db.commit()
            await db.refresh(session)
            await self._populate_metadata(db, [session])
            return session

    async def delete_owned(self, *, session_id: UUID, user_id: int) -> str | None:
        """异步删除用户拥有的非 active 会话，返回错误 key 或 None。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.session_id == session_id,
                    Session.user_id == user_id,
                )
            )
            session = result.scalar_one_or_none()
            if session is None:
                return "not_found"
            if session.status == "active":
                return "active"
            await db.execute(update(Position).where(Position.session_id == session_id).values(session_id=None))
            await db.execute(update(Order).where(Order.session_id == session_id).values(session_id=None))
            await db.execute(update(TradeRecord).where(TradeRecord.session_id == session_id).values(session_id=None))
            await db.execute(delete(DebateMessage).where(DebateMessage.session_id == session_id))
            await db.delete(session)
            await db.commit()
            return None

    async def batch_delete_owned(self, *, session_ids: list[UUID], user_id: int) -> tuple[int, int]:
        """异步批量删除用户拥有的非 active 会话。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.session_id.in_(session_ids),
                    Session.user_id == user_id,
                )
            )
            sessions = result.scalars().all()
            to_delete_ids = [s.session_id for s in sessions if s.status != "active"]
            active_count = sum(1 for s in sessions if s.status == "active")
            if not to_delete_ids:
                return 0, active_count
            await db.execute(update(Position).where(Position.session_id.in_(to_delete_ids)).values(session_id=None))
            await db.execute(update(Order).where(Order.session_id.in_(to_delete_ids)).values(session_id=None))
            await db.execute(update(TradeRecord).where(TradeRecord.session_id.in_(to_delete_ids)).values(session_id=None))
            await db.execute(delete(DebateMessage).where(DebateMessage.session_id.in_(to_delete_ids)))
            await db.execute(delete(Session).where(Session.session_id.in_(to_delete_ids)))
            await db.commit()
            return len(to_delete_ids), active_count

crud_session = CRUDSession(Session)
