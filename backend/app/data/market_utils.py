from datetime import datetime, time
import pytz


def is_trading_day() -> bool:
    """
    判断当前是否为交易日（初步实现：周一至周五）
    Determine if today is a trading day (Initial implementation: Monday to Friday)
    """
    # 使用上海时区
    shanghai_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(shanghai_tz)
    # 0 是周一, 4 是周五, 5 是周六, 6 是周日
    return now.weekday() < 5


def is_trading_time() -> bool:
    """
    判断当前是否在 A 股交易时间段内
    Determine if current time is within A-share trading hours
    - 早盘: 09:15 - 11:30 (包含集合竞价)
    - 午盘: 13:00 - 15:00
    """
    if not is_trading_day():
        return False

    shanghai_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(shanghai_tz).time()

    # 定义时段
    morning_start = time(9, 15)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    # 判断是否在时段内
    in_morning = morning_start <= now <= morning_end
    in_afternoon = afternoon_start <= now <= afternoon_end

    return in_morning or in_afternoon
