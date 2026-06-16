# Tushare 全局限流器

## 概述

为防止超出 Tushare Pro API 的调用频率限制，本项目实现了基于令牌桶算法的全局限流器。

## 限流规则

根据 Tushare Pro 官方文档（https://tushare.pro/document/1?doc_id=290），不同积分等级对应不同的每分钟调用限制：

| 积分等级 | 每分钟调用次数 | 每天总量限制 |
|---------|---------------|-------------|
| 120     | 50次          | 8,000次     |
| 2000+   | 200次         | 100,000次/单个API |
| 5000+   | 500次         | 常规数据无上限 |
| 10000+  | 500次         | 常规数据无上限，特色数据300次/分钟 |
| 15000+  | 500次         | 特色数据无总量限制 |

## 配置方式

在 `backend/.env` 中设置你的 Tushare 积分等级：

```bash
# Tushare 配置
TUSHARE_TOKEN=your-tushare-token
TUSHARE_API=http://api.waditu.com/dataapi
TUSHARE_CREDITS=5000  # 默认 5000 分
```

## 实现原理

### 令牌桶算法

- 初始化时，令牌桶填满（数量 = 每分钟最大调用次数）
- 每次 API 调用前，尝试从桶中获取 1 个令牌
- 令牌按固定速率补充（每分钟补充 `max_calls_per_minute` 个）
- 如果桶中没有令牌，调用会等待直到有令牌可用

### 全局单例

限流器采用全局单例模式，确保所有 Tushare API 调用共享同一个限流器：

```python
from app.data.ingestors.rate_limiter import get_tushare_rate_limiter

limiter = get_tushare_rate_limiter()
```

### 自动集成

`TushareIngestor` 类在初始化时自动获取全局限流器，所有通过 `_run_in_executor` 调用的 Tushare API 都会自动受到限流保护。

## 使用示例

### 基本用法

```python
# 在 TushareIngestor 中已自动集成
async def fetch_data(self):
    # 自动限流，无需手动处理
    df = await self._run_in_executor(self.pro.daily, ts_code='000001.SZ')
    return df
```

### 手动使用限流器

如果需要在其他地方使用限流器：

```python
from app.data.ingestors.rate_limiter import get_tushare_rate_limiter

limiter = get_tushare_rate_limiter()

# 异步阻塞获取令牌
acquired = await limiter.acquire(timeout=30.0)
if acquired:
    # 执行 API 调用
    result = some_tushare_api_call()
else:
    # 超时处理
    logger.warning("Rate limiter timeout")
```

### 非阻塞尝试

```python
# 非阻塞尝试获取令牌
if limiter.try_acquire():
    # 立即执行
    result = some_tushare_api_call()
else:
    # 稍后重试
    pass
```

### 查询等待时间

```python
# 获取下一个令牌需要等待的时间
wait_time = limiter.get_wait_time()
print(f"需要等待 {wait_time:.2f} 秒")
```

## 监控与日志

限流器在初始化和关键操作时会输出日志：

```
2026-06-16 15:48:48 [INFO] TushareRateLimiter initialized | credits=5000 max_calls_per_minute=500
2026-06-16 15:50:00 [WARNING] Tushare rate limiter timeout after 30s | func=daily
```

## 测试

运行限流器单元测试：

```bash
cd backend
python -m pytest tests/test_tushare_rate_limiter.py -v
```

## 常见问题

### Q: 我的积分等级是多少？

A: 登录 Tushare Pro 官网 (https://tushare.pro)，在个人中心查看积分。

### Q: 如何调整限流速率？

A: 修改 `.env` 文件中的 `TUSHARE_CREDITS` 值，重启应用即可。

### Q: 限流器会影响性能吗？

A: 影响极小。限流器使用高效的令牌桶算法，仅在接近限流阈值时才会引入延迟。

### Q: 如果我有多个 Tushare Token 怎么办？

A: 当前实现只支持单个 Token。如果需要多 Token 负载均衡，需要额外实现。

## 相关文件

- **限流器实现**: `backend/app/data/ingestors/rate_limiter.py`
- **集成代码**: `backend/app/data/ingestors/plugins/tushare_ingestor.py`
- **配置文件**: `backend/app/core/config.py`
- **单元测试**: `backend/tests/test_tushare_rate_limiter.py`
- **配置示例**: `backend/.env.example`

## 参考资料

- [Tushare Pro 积分权限说明](https://tushare.pro/document/1?doc_id=108)
- [Tushare Pro 积分频次对应表](https://tushare.pro/document/1?doc_id=290)
