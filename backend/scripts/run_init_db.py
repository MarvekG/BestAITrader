import sys
import os
import time
from multiprocessing import Process

# 设置PYTHONPATH为当前目录
sys.path.insert(0, os.path.abspath('.'))

# 导入init_db模块
from app.core import init_db
from app.core.logger import get_logger

# 获取日志记录器
logger = get_logger(__name__)

def run_init_db():
    """运行数据库初始化函数"""
    print("Creating database session...")
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    try:
        print("Running init_db...")
        init_db.init_db(db)
        print("Checking users...")
        users = db.query(User).all()
        print(f"Found {len(users)} users:")
        for user in users:
            print(f"  - {user.username} ({user.email}) - Active: {user.is_active}, Superuser: {user.is_superuser}")
    finally:
        print("Closing database session...")
        db.close()

if __name__ == "__main__":
    logger.info("Starting database initialization...")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"PYTHONPATH: {sys.path}")
    
    # 创建进程执行init_db
    p = Process(target=run_init_db)
    p.start()
    
    # 设置超时时间为60秒
    timeout = 60
    start_time = time.time()
    
    # 等待进程完成或超时
    p.join(timeout)
    
    # 检查进程是否仍在运行
    if p.is_alive():
        logger.error(f"Error: Database initialization execution time exceeded {timeout} seconds, terminating process...")
        p.terminate()
        p.join()  # 等待进程真正结束
        logger.info("Process terminated")
        logger.info("\nPossible reasons:")
        logger.info("1. PostgreSQL database not started or connection failed")
        logger.info("2. Database connection configuration error")
        logger.info("3. Database performance issue or network delay")
        logger.info("\nRecommended solutions:")
        logger.info("1. Check if PostgreSQL is running: docker-compose ps")
        logger.info("2. Check if database connection configuration is correct")
        logger.info("3. Try restarting PostgreSQL container: docker-compose restart db")
        logger.info("4. Check database logs: docker-compose logs db")
        sys.exit(1)
    else:
        logger.info(f"Database initialization completed, total time: {time.time() - start_time:.2f} seconds")
        sys.exit(0)
