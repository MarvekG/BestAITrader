from fastapi import HTTPException, status

from app.core.config import settings


def require_database_backup_import_enabled() -> None:
    """
    Require deployment config to allow database backup and import endpoints.

    Raises:
        HTTPException: When database backup and import endpoints are disabled.
    """
    if not settings.ENABLE_MAINTENANCE_ENDPOINTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
