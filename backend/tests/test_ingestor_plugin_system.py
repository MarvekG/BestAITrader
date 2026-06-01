import pytest

from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestors.plugin_loader import (
    discover_ingestor_plugin_classes,
    validate_ingestor_registration,
)


class DummyIngestor(BaseIngestor):
    source_name = "dummy"
    display_name = "Dummy"


class AnotherDummyIngestor(BaseIngestor):
    source_name = "dummy"
    display_name = "Another Dummy"


class TestIngestorPluginDiscovery:
    """Tests for ingestor plugin discovery."""

    def test_discovery_finds_tushare_plugin(self):
        """Discovery should find the built-in Tushare plugin."""
        plugin_classes = discover_ingestor_plugin_classes()
        source_names = {plugin_class.get_source_name() for plugin_class in plugin_classes}

        assert "tushare" in source_names


class TestIngestorValidation:
    """Tests for manager-side plugin validation."""

    def test_validation_accepts_unique_source(self):
        """Validation should allow a plugin with a unique source name."""
        validate_ingestor_registration(DummyIngestor(), set())

    def test_register_rejects_duplicate_source_name(self):
        """Validation should reject duplicate source names."""
        with pytest.raises(ValueError, match="Duplicate ingestor source_name"):
            validate_ingestor_registration(AnotherDummyIngestor(), {"dummy"})
