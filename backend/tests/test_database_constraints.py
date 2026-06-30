from app.models.session import Session
from app.models.system_setting import SystemSetting


def test_session_user_id_is_required_and_foreign_keyed():
    """Debate 会话必须始终归属到用户。"""
    user_id_column = Session.__table__.c.user_id

    assert user_id_column.nullable is False
    assert user_id_column.foreign_keys


def test_system_setting_user_id_is_optional_but_foreign_keyed():
    """系统设置支持全局配置和用户级配置，用户级配置必须受外键约束。"""
    user_id_column = SystemSetting.__table__.c.user_id

    assert user_id_column.nullable is True
    assert user_id_column.foreign_keys
