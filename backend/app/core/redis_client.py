import redis.asyncio as redis
from typing import Optional, Any
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

class RedisClient:
    def __init__(self):
        self.redis_url = settings.REDIS_URL
        self.redis = None
        self.pool = None
    
    async def init_pool(self):
        """初始化Redis连接池"""
        try:
            self.pool = redis.ConnectionPool.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            self.redis = redis.Redis(connection_pool=self.pool)
            await self.redis.ping()
            logger.info(f"Redis connection successful: {self.redis_url}")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}", exc_info=True)
            # When connection fails, do not raise exception, set redis to None
            # This allows graceful degradation when Redis is unavailable
            self.redis = None
            self.pool = None
            logger.warning("Redis connection failed, running in no-cache mode")
    
    async def close(self):
        """Close Redis connection"""
        if self.redis:
            await self.redis.close()
        if self.pool:
            await self.pool.disconnect()
        logger.info("Redis connection closed")
    
    async def get(self, key: str) -> Optional[str]:
        """Get cached data"""
        if self.redis is None:
            return None
        try:
            return await self.redis.get(key)
        except Exception as e:
            logger.error(f"Failed to get Redis cache, key: {key}, error: {e}")
            return None
    
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """Set cached data"""
        if self.redis is None:
            return False
        try:
            if expire:
                await self.redis.setex(key, expire, value)
            else:
                await self.redis.set(key, value)
            return True
        except Exception as e:
            logger.error(f"Failed to set Redis cache, key: {key}, error: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete cached data"""
        if self.redis is None:
            return False
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Redis cache, key: {key}, error: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """Check if cache exists"""
        if self.redis is None:
            return False
        try:
            return await self.redis.exists(key) > 0
        except Exception as e:
            logger.error(f"Failed to check Redis cache existence, key: {key}, error: {e}")
            return False
    
    async def clear_pattern(self, pattern: str) -> int:
        """Delete all caches matching pattern"""
        if self.redis is None:
            return 0
        try:
            keys = await self.redis.keys(pattern)
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Failed to delete Redis cache matching pattern, pattern: {pattern}, error: {e}")
            return 0
    
    async def lpush(self, key: str, *values: Any) -> int:
        """Push value to list"""
        if self.redis is None:
            return 0
        try:
            return await self.redis.lpush(key, *values)
        except Exception as e:
            logger.error(f"Failed to lpush to Redis callback, key: {key}: {e}")
            return 0

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Get a Redis list range."""
        if self.redis is None:
            return []
        try:
            return await self.redis.lrange(key, start, end)
        except Exception as e:
            logger.error(f"Failed to lrange Redis list, key: {key}: {e}")
            return []

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim a Redis list to a range."""
        if self.redis is None:
            return False
        try:
            await self.redis.ltrim(key, start, end)
            return True
        except Exception as e:
            logger.error(f"Failed to ltrim Redis list, key: {key}: {e}")
            return False

    async def expire(self, key: str, seconds: int) -> bool:
        """Set key expiration"""
        if self.redis is None:
            return False
        try:
            return await self.redis.expire(key, seconds)
        except Exception as e:
            logger.error(f"Failed to set expire for Redis key {key}: {e}")
            return False
            
    async def publish(self, channel: str, message: str) -> int:
        """Publish message to channel"""
        if self.redis is None:
            return 0
        try:
            return await self.redis.publish(channel, message)
        except Exception as e:
            logger.error(f"Failed to publish to Redis channel {channel}: {e}")
            return 0

# 创建全局Redis客户端实例
redis_client = RedisClient()
