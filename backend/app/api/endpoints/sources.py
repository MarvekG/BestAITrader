from fastapi import APIRouter, HTTPException, status
from typing import Dict, Any, Optional

from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.data.ingestors.manager import ingestor_manager
from app.core.config import settings
from app.core.logger import get_logger

from app.core.i18n import i18n_service
    
# 获取自己的代码的logger
logger = get_logger(__name__)

router = APIRouter()


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
