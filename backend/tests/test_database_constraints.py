from app.ai.stock_picker.models import StockSelectionRun
from app.models.session import Session as AnalysisSession
from app.models.system_setting import SystemSetting


def test_sessions_user_id_is_required():
    """Sessions must always be owned by a user at the schema model layer."""
    assert AnalysisSession.__table__.c.user_id.nullable is False


def test_stock_selection_runs_user_id_references_users():
    """Stock picker runs must enforce a database-level user owner reference."""
    foreign_keys = StockSelectionRun.__table__.c.user_id.foreign_keys

    assert {foreign_key.target_fullname for foreign_key in foreign_keys} == {"users.id"}


def test_system_settings_can_be_owned_by_user():
    """User-scoped runtime settings must have an explicit database owner column."""
    assert "user_id" in SystemSetting.__table__.c

    foreign_keys = SystemSetting.__table__.c.user_id.foreign_keys
    assert {foreign_key.target_fullname for foreign_key in foreign_keys} == {"users.id"}
