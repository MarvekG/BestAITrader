from pathlib import Path
import tempfile
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from typing import Dict, Any, Optional

from app.api.maintenance import require_database_backup_import_enabled
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.data.ingestors.manager import ingestor_manager
from app.core.config import settings
from app.core.logger import get_logger
from app.core.database_maintenance import (
    create_database_backup,
    restore_database_backup,
    schedule_backend_reload,
)

from app.core.i18n import i18n_service
    
# 获取自己的代码的logger
logger = get_logger(__name__)

router = APIRouter()


def _cleanup_temp_file(path: Path) -> None:
    path.unlink(missing_ok=True)


@router.get("/", response_model=Dict[str, Any])
async def list_data_sources():
    """获取所有已注册的数据源及当前默认数据源"""
    try:
        sources = ingestor_manager.list_data_sources()
        source_details = ingestor_manager.list_data_source_details()
        default_source = ingestor_manager.default_source
        prioritized = ingestor_manager.get_prioritized_sources()
        return {
            "status": "success",
            "sources": sources,
            "source_details": source_details,
            "default_source": default_source,
            "priority_order": prioritized,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.get_list_failed')}: {str(e)}"
        )


@router.post("/default", response_model=Dict[str, Any])
async def set_default_data_source(source_name: str):
    """设置默认数据源"""
    if ingestor_manager.set_default_source(source_name):
        return {
            "status": "success",
            "message": i18n_service.t("sources.default_set_success").format(source_name=source_name),
            "default_source": ingestor_manager.default_source
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=i18n_service.t("sources.not_found").format(source_name=source_name)
        )


@router.get("/tushare/config", response_model=Dict[str, Any])
async def get_tushare_config():
    """获取当前Tushare配置"""
    try:
        config = TushareIngestor.get_tushare_config()
        return {
            "status": "success",
            "config": config
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.get_config_failed')}: {str(e)}"
        )


from pydantic import BaseModel

class TushareConfigUpdate(BaseModel):
    token: Optional[str] = None
    api_url: Optional[str] = None

@router.post("/tushare/config", response_model=Dict[str, Any])
async def update_tushare_config(
    config: TushareConfigUpdate,
):
    """更新Tushare配置"""
    try:
        from app.core.env_manager import env_manager
        
        token = config.token
        api_url = config.api_url
        
        # 1. Update Env File
        if token:
            if not env_manager.set_key("TUSHARE_TOKEN", token):
                logger.error(i18n_service.t("sources.update_token_failed"))
        
        if api_url:
            if not env_manager.set_key("TUSHARE_API", api_url):
                logger.error(i18n_service.t("sources.update_api_failed"))
            # Update settings in memory
            settings.TUSHARE_API = api_url
        
        # 2. Update Runtime Instance
        ingestor = ingestor_manager.get_ingestor("tushare")
        if ingestor and token:
            ingestor.update_token(token)
            
        # 3. Call legacy update for API URL handling (if any) or just return result
        # Since we modified TushareIngestor.update_tushare_config to be deprecated/compatible
        # We can still call it for api_url or constructing the response
        
        updated_config = TushareIngestor.update_tushare_config(token=token, api_url=api_url)
        
        return {
            "status": "success",
            "message": i18n_service.t("sources.config_updated"),
            "updated_config": updated_config
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.update_config_failed')}: {str(e)}"
        )


@router.get("/database/backup")
async def download_database_backup(
    _: None = Depends(require_database_backup_import_enabled),
):
    """导出当前 PostgreSQL 数据库为自定义归档备份文件"""
    try:
        backup_path, download_name = create_database_backup()
        return FileResponse(
            path=backup_path,
            media_type="application/octet-stream",
            filename=download_name,
            background=BackgroundTask(_cleanup_temp_file, backup_path),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to create database backup")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database backup failed: {str(e)}",
        )


@router.post("/database/import", response_model=Dict[str, Any])
async def import_database_backup(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_database_backup_import_enabled),
    file: UploadFile = File(...),
):
    """导入 PostgreSQL .dump 备份文件到当前数据库"""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No backup file provided.",
        )
    file_suffix = Path(file.filename).suffix.lower()
    if file_suffix != ".dump":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .dump backup files are supported.",
        )

    temp_path: Path | None = None
    uploaded_bytes = 0
    started_at = time.perf_counter()
    try:
        logger.info(
            "Received database import request",
            extra={
                "operation": "database_import",
                "upload_filename": file.filename,
                "content_type": file.content_type,
            },
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await file.read(1024 * 1024):
                temp_file.write(chunk)
                uploaded_bytes += len(chunk)

        logger.info(
            "Database import upload saved to temp file",
            extra={
                "operation": "database_import",
                "upload_filename": file.filename,
                "temp_path": str(temp_path),
                "size_bytes": uploaded_bytes,
                "duration_seconds": round(time.perf_counter() - started_at, 3),
            },
        )

        if temp_path.stat().st_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup file is empty.",
            )

        restore_database_backup(temp_path)
        background_tasks.add_task(schedule_backend_reload)
        logger.info(
            "Database import completed; backend reload scheduled",
            extra={
                "operation": "database_import",
                "upload_filename": file.filename,
                "temp_path": str(temp_path),
                "size_bytes": uploaded_bytes,
                "duration_seconds": round(time.perf_counter() - started_at, 3),
            },
        )
        return {
            "status": "success",
            "message": "Database import completed. Backend restart scheduled.",
            "filename": file.filename,
            "restart_scheduled": True,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to import database backup")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database import failed: {str(e)}",
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        await file.close()
