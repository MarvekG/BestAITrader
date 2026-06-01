from typing import Dict, Set, Any, Optional
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
import json
import asyncio
from datetime import datetime
from app.data.storage import data_storage_service
from app.core.logger import get_logger

logger = get_logger(__name__)


class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # Extend subscription mechanism, support multiple event types
        self.subscriptions: Dict[str, Dict[str, Set[str]]] = {}
        self.price_update_task: Optional[asyncio.Task] = None

        self.heartbeat_task: Optional[asyncio.Task] = None
        self.notification_task: Optional[asyncio.Task] = None
        self.market_watch_event_task: Optional[asyncio.Task] = None

    async def connect(self, websocket: WebSocket, session_id: str):
        logger.info(f"WebSocket connect attempt: {session_id}")
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = set()
        self.active_connections[session_id].add(websocket)
        logger.info(f"WebSocket connected for session: {session_id}. Total sessions: {len(self.active_connections)}")

        # If heartbeat task is not started, start it
        if not self.heartbeat_task or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self._run_heartbeat())

        # If notification task is not started, start it
        if not self.notification_task or self.notification_task.done():
            self.notification_task = asyncio.create_task(self._run_task_notifications())

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            self.active_connections[session_id].discard(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
                logger.info(f"WebSocket session removed: {session_id}")

        if session_id in self.subscriptions and session_id not in self.active_connections:
            del self.subscriptions[session_id]

        # If no active connections, cancel all tasks
        if not self.active_connections:
            if self.price_update_task and not self.price_update_task.done():
                self.price_update_task.cancel()
                self.price_update_task = None
            if self.heartbeat_task and not self.heartbeat_task.done():
                self.heartbeat_task.cancel()
                self.heartbeat_task = None
            if self.notification_task and not self.notification_task.done():
                self.notification_task.cancel()
                self.notification_task = None
            if self.market_watch_event_task and not self.market_watch_event_task.done():
                self.market_watch_event_task.cancel()
                self.market_watch_event_task = None

    async def connect_market_watch(self, websocket: WebSocket, user_id: int) -> str:
        """
        Accept a market watch WebSocket connection scoped to one user.

        Args:
            websocket: Incoming WebSocket connection.
            user_id: Authenticated user id.

        Returns:
            Internal connection group id.
        """
        session_id = self._market_watch_session_id(user_id)
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = set()
        self.active_connections[session_id].add(websocket)

        if not self.market_watch_event_task or self.market_watch_event_task.done():
            self.market_watch_event_task = asyncio.create_task(self._run_market_watch_events())
        return session_id

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: Dict[str, Any]):
        payload = json.dumps(message)
        for session_id, connections in list(self.active_connections.items()):
            for connection in list(connections):
                await self._safe_send_text(connection, session_id, payload)

    async def broadcast_to_session(self, message: Dict[str, Any], session_id: str):
        if session_id in self.active_connections:
            payload = json.dumps(message)
            for connection in list(self.active_connections[session_id]):
                await self._safe_send_text(connection, session_id, payload)

    def subscribe(self, session_id: str, event_type: str, resource_id: str = "*"):
        """
        订阅事件类型

        Args:
            session_id: 会话ID
            event_type: 事件类型（price, debate, order, market等）
            resource_id: 资源ID（股票代码等，默认为"*"表示所有资源）
        """
        if session_id not in self.subscriptions:
            self.subscriptions[session_id] = {}
        if event_type not in self.subscriptions[session_id]:
            self.subscriptions[session_id][event_type] = set()

        self.subscriptions[session_id][event_type].add(resource_id)
        logger.info(f"Session {session_id} subscribed to {event_type} (resource: {resource_id})")

        # 如果订阅的是价格事件，启动价格更新任务
        if event_type == "price" and (not self.price_update_task or self.price_update_task.done()):
            self.price_update_task = asyncio.create_task(self._run_price_updates())

    def unsubscribe(self, session_id: str, event_type: str, resource_id: str = "*"):
        """
        取消订阅事件类型

        Args:
            session_id: 会话ID
            event_type: 事件类型
            resource_id: 资源ID，默认为"*"表示取消所有该类型订阅
        """
        if session_id in self.subscriptions and event_type in self.subscriptions[session_id]:
            if resource_id == "*":
                del self.subscriptions[session_id][event_type]
            else:
                self.subscriptions[session_id][event_type].discard(resource_id)

                # 如果该事件类型没有订阅了，删除它
                if not self.subscriptions[session_id][event_type]:
                    del self.subscriptions[session_id][event_type]

        # 如果没有订阅价格事件了，取消价格更新任务
        price_subscribed = any(
            "price" in event_types
            for event_types in self.subscriptions.values()
        )
        if not price_subscribed and self.price_update_task and not self.price_update_task.done():
            self.price_update_task.cancel()
            self.price_update_task = None

    def subscribe_price(self, session_id: str, stock_code: str):
        """兼容旧的价格订阅方法"""
        self.subscribe(session_id, "price", stock_code)

    def unsubscribe_price(self, session_id: str, stock_code: str):
        """兼容旧的价格取消订阅方法"""
        self.unsubscribe(session_id, "price", stock_code)

    async def _safe_send_text(self, websocket: WebSocket, session_id: str, payload: str) -> bool:
        try:
            await websocket.send_text(payload)
            return True
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected while sending message for session: {session_id}")
        except Exception as e:
            logger.warning(f"WebSocket send failed for session {session_id}: {e}")

        self.disconnect(websocket, session_id)
        return False

    async def _run_heartbeat(self):
        """Run heartbeat task, send heartbeat every 30 seconds"""
        while True:
            try:
                # Send heartbeat to all active connections
                message = {
                    "type": "heartbeat",
                    "timestamp": datetime.now().isoformat()
                }
                await self.broadcast(message)
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds
            except asyncio.CancelledError:
                logger.info("Heartbeat detection task cancelled")
                break
            except Exception as e:
                logger.error(f"Heartbeat detection task error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Rest for 5 seconds before retrying if error occurs

    async def _run_price_updates(self):
        """Run price update task"""
        while True:
            try:
                # Get all subscribed stock codes
                all_subscribed_stocks = set()
                for session_subscriptions in self.subscriptions.values():
                    if "price" in session_subscriptions:
                        all_subscribed_stocks.update(session_subscriptions["price"])

                if not all_subscribed_stocks:
                    await asyncio.sleep(5)  # If no subscriptions, rest for 5 seconds
                    continue

                logger.debug(f"Start updating prices for {len(all_subscribed_stocks)} stocks")

                # Update and push price for each stock
                for stock_code in all_subscribed_stocks:
                    try:
                        # Use DataStorageService (sync) to get realtime price from DB
                        realtime_data = data_storage_service.get_stock_realtime_market(stock_code)
                        if realtime_data and realtime_data.get("latest_price"):
                            price_update = {
                                "stock_code": stock_code,
                                "stock_name": realtime_data.get("name"),  # Might be None if not joined
                                "current_price": realtime_data.get("latest_price"),
                                "change": realtime_data.get("change_amount"),
                                "change_pct": realtime_data.get("change_percent"),
                                "timestamp": datetime.now().isoformat()
                            }
                            await self.send_price_update(stock_code, price_update)
                    except Exception as e:
                        logger.error(f"Failed to update price for stock {stock_code}: {e}")

                await asyncio.sleep(3)  # Update prices every 3 seconds
            except asyncio.CancelledError:
                logger.info("Price update task cancelled")
                break
            except Exception as e:
                logger.error(f"Price update task error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Rest for 1 second before retrying if error occurs

    async def _run_task_notifications(self):
        """Run task notification subscription task"""
        from app.core.config import settings
        import redis.asyncio as redis

        logger.info("Starting task notification loop")
        redis_conn = None
        pubsub = None

        while True:
            try:
                # Create a dedicated Redis connection for subscription
                redis_conn = redis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True
                )
                pubsub = redis_conn.pubsub()
                await pubsub.subscribe("task_notifications")
                logger.info("Subscribed to task_notifications channel")

                async for message in pubsub.listen():
                    # logger.info(f"Received Redis message: {message}") # Debug log
                    if message["type"] == "message":
                        logger.info("Processing task notification message from Redis")
                        try:
                            data = json.loads(message["data"])
                            await self.send_task_notification(
                                task_id=data.get("task_id"),
                                task_name=data.get("task_name"),
                                status=data.get("status"),
                                result=data.get("result"),
                                error_message=data.get("error_message")
                            )
                            logger.info(f"Forwarded task notification to WebSocket: {data.get('task_id')}")
                        except json.JSONDecodeError:
                            logger.error(f"Failed to decode task notification: {message['data']}")
                        except Exception as e:
                            logger.error(f"Error processing task notification: {e}", exc_info=True)

            except asyncio.CancelledError:
                logger.info("Task notification subscription cancelled")
                break
            except Exception as e:
                logger.error(f"Task notification subscription error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Retry after 5 seconds
            finally:
                if pubsub:
                    close_pubsub = getattr(pubsub, "aclose", pubsub.close)
                    await close_pubsub()
                if redis_conn:
                    close_redis = getattr(redis_conn, "aclose", redis_conn.close)
                    await close_redis()

    async def _run_market_watch_events(self):
        """Forward Redis market watch events and rendered source documents to WebSocket connections."""
        from app.core.config import settings
        from app.ai.market_watch.audit import MARKET_WATCH_DOCUMENTS_CHANNEL, MARKET_WATCH_EVENTS_CHANNEL
        import redis.asyncio as redis

        logger.info("Starting market watch event notification loop")
        redis_conn = None
        pubsub = None

        while True:
            try:
                redis_conn = redis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                )
                pubsub = redis_conn.pubsub()
                await pubsub.subscribe(MARKET_WATCH_EVENTS_CHANNEL, MARKET_WATCH_DOCUMENTS_CHANNEL)
                logger.info(
                    "Subscribed to %s and %s channels",
                    MARKET_WATCH_EVENTS_CHANNEL,
                    MARKET_WATCH_DOCUMENTS_CHANNEL,
                )

                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        if message.get("channel") == MARKET_WATCH_DOCUMENTS_CHANNEL:
                            await self.send_market_watch_documents(payload)
                        else:
                            await self.send_market_watch_event(payload)
                    except json.JSONDecodeError:
                        logger.error("Failed to decode market watch event: %s", message["data"])
                    except Exception as e:
                        logger.error("Error processing market watch event: %s", e, exc_info=True)

            except asyncio.CancelledError:
                logger.info("Market watch event subscription cancelled")
                break
            except Exception as e:
                logger.error("Market watch event subscription error: %s", e, exc_info=True)
                await asyncio.sleep(5)
            finally:
                if pubsub:
                    close_pubsub = getattr(pubsub, "aclose", pubsub.close)
                    await close_pubsub()
                if redis_conn:
                    close_redis = getattr(redis_conn, "aclose", redis_conn.close)
                    await close_redis()

    @staticmethod
    def _market_watch_session_id(user_id: int) -> str:
        return f"market-watch:{user_id}"

    async def send_market_watch_event(self, event: Dict[str, Any]):
        """Send a market watch event only to the owning user's connections."""
        user_id = event.get("user_id")
        if user_id is None:
            logger.warning("Skipping market watch event without user_id")
            return

        message = {
            "type": "market_watch_event",
            "event": event,
            "timestamp": datetime.now().isoformat(),
        }
        await self.broadcast_to_session(message, self._market_watch_session_id(int(user_id)))

    async def send_market_watch_documents(self, payload: Dict[str, Any]):
        """Send freshly rendered source documents only to the owning user's connections."""
        user_id = payload.get("user_id")
        documents = payload.get("documents") or []
        if user_id is None or not documents:
            return
        message = {
            "type": "market_watch_documents",
            "documents": documents,
            "timestamp": datetime.now().isoformat(),
        }
        await self.broadcast_to_session(message, self._market_watch_session_id(int(user_id)))

    async def send_price_update(self, stock_code: str, price_data: Dict[str, Any]):
        """Send price update"""
        message = {
            "type": "price_update",
            "data": {
                "stock_code": stock_code,
                **price_data
            },
            "timestamp": datetime.now().isoformat()
        }

        # Send to sessions subscribed to this stock or all stocks
        for session_id, subscriptions in self.subscriptions.items():
            if "price" in subscriptions:
                subscribed_stocks = subscriptions["price"]
                if stock_code in subscribed_stocks or "*" in subscribed_stocks:
                    await self.broadcast_to_session(message, session_id)

    async def send_order_filled(self, session_id: str, order_data: Dict[str, Any]):
        """Send order filled notification"""
        message = {
            "type": "order_filled",
            "data": order_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_debate_update(self, session_id: str, debate_data: Dict[str, Any]):
        """Send debate update"""
        message = {
            "type": "debate_update",
            "data": debate_data,
            "timestamp": datetime.now().isoformat()
        }

        # Send to sessions subscribed to debate events
        if session_id in self.subscriptions and "debate" in self.subscriptions[session_id]:
            await self.broadcast_to_session(message, session_id)

    async def send_market_update(self, market_data: Dict[str, Any]):
        """Send market update"""
        message = {
            "type": "market_update",
            "data": market_data,
            "timestamp": datetime.now().isoformat()
        }

        # Send to sessions subscribed to market events
        for session_id, subscriptions in self.subscriptions.items():
            if "market" in subscriptions:
                await self.broadcast_to_session(message, session_id)

    async def send_trade_executed(self, session_id: str, trade_data: Dict[str, Any]):
        """Send trade executed notification"""
        message = {
            "type": "trade_executed",
            "data": trade_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_alert(self, session_id: str, alert_data: Dict[str, Any]):
        """Send alert notification"""
        message = {
            "type": "alert",
            "data": alert_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_order_status(self, session_id: str, order_status_data: Dict[str, Any]):
        """Send order status update"""
        message = {
            "type": "order_status",
            "data": order_status_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_position_update(self, session_id: str, position_data: Dict[str, Any]):
        """Send position update"""
        message = {
            "type": "position_update",
            "data": position_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_session_update(self, session_id: str, session_data: Dict[str, Any]):
        """Send session update"""
        message = {
            "type": "session_update",
            "data": session_data,
            "timestamp": datetime.now().isoformat()
        }
        await self.broadcast_to_session(message, session_id)

    async def send_task_notification(
        self,
        task_id: str,
        task_name: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ):
        """发送任务通知

        Args:
            task_id: 任务ID
            task_name: 任务名称
            status: 任务状态 (completed/failed)
            result: 任务结果
            error_message: 错误信息
        """
        message = {
            "type": "task_completed",
            "data": {
                "task_id": task_id,
                "task_name": task_name,
                "status": status,
                "result": result,
                "error_message": error_message
            },
            "timestamp": datetime.now().isoformat()
        }
        # Broadcast to all active connections
        await self.broadcast(message)

    async def send_agentic_update(self, task_id: str, message: str, stage: str = "analysis", notif_type: str = "log"):
        """
        发送 Agentic 运行过程更新 (Send Agentic execution update)
        """
        update_msg = {
            "type": "agentic_update",
            "data": {
                "task_id": task_id,
                "message": message,
                "stage": stage,
                "type": notif_type,
                "timestamp": datetime.now().isoformat()
            }
        }
        # 广播给所有订阅了 agentic 事件的会话
        # Broadcast to all sessions subscribed to agentic events
        for session_id, subs in self.subscriptions.items():
            if "agentic" in subs:
                await self.broadcast_to_session(update_msg, session_id)

    async def send_stock_picker_update(
        self,
        run_id: str,
        stage: str,
        status: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ):
        update_msg = {
            "type": "stock_picker_update",
            "data": {
                "run_id": run_id,
                "stage": stage,
                "status": status,
                "message": message,
                "payload": payload or {},
                "timestamp": datetime.now().isoformat(),
            },
        }
        for session_id, subs in self.subscriptions.items():
            if "stock_picker" in subs:
                await self.broadcast_to_session(update_msg, session_id)

    async def send_experience_review_update(
        self,
        debate_session_id: str,
        review_run_id: Optional[str],
        stage: str,
        status: str,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
        message_key: Optional[str] = None,
        message_params: Optional[Dict[str, Any]] = None,
        ):
        update_msg = {
            "type": "experience_review_update",
            "data": {
                "debate_session_id": debate_session_id,
                "review_run_id": review_run_id,
                "stage": stage,
                "status": status,
                "message": message,
                "message_key": message_key,
                "message_params": message_params or {},
                "payload": payload or {},
                "timestamp": datetime.now().isoformat(),
            },
        }
        for session_id, subs in self.subscriptions.items():
            experience_review_resources = subs.get("experience_review")
            if not experience_review_resources:
                continue
            if "*" not in experience_review_resources and debate_session_id not in experience_review_resources:
                continue
            await self.broadcast_to_session(update_msg, session_id)


ws_manager = WebSocketManager()
