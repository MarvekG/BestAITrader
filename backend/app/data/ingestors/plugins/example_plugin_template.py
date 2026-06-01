"""
Template for adding a new local ingestor plugin.

Rename the file to ``<source>_ingestor.py`` when you want it to be auto-discovered.
"""

import pandas as pd

from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestion.service import DataIngestionService
from app.core.utils.formatters import StockCodeStandardizer


class ExampleIngestor(BaseIngestor):
    """Minimal example ingestor implementation."""

    source_name = "example"
    display_name = "Example"
    required_settings = ()

    API_BASE_URL = "https://api.example.com"
    API_TOKEN = ""

    def __init__(self) -> None:
        self.ingestion_service = DataIngestionService()
        self.source = self.get_source_name()
        self.client = self._build_client()

    def _build_client(self):
        """Create provider client without relying on project settings."""
        return object()

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> bool:
        """
        Example implementation.

        Args:
            stock_code: Standardized stock code.

        Returns:
            True when ingestion succeeds.
        """
        standardized_code = StockCodeStandardizer.standardize(stock_code)

        df = pd.DataFrame(
            [
                {
                    "stock_code": standardized_code,
                    "current_price": None,
                    "change_percent": None,
                    "turnover_rate": None,
                    "volume_ratio": None,
                    "amplitude": None,
                    "pb_ratio": None,
                    "pe_dynamic": None,
                    "turnover": None,
                    "volume": None,
                    "total_market_cap": None,
                    "circulating_market_cap": None,
                    "timestamp": None,
                    "data_source": self.source,
                }
            ]
        )
        if df.empty:
            return False

        df["stock_code"] = df["stock_code"].apply(StockCodeStandardizer.standardize)
        df["data_source"] = self.source

        await self._run_in_executor(
            self.ingestion_service.write_dataframe,
            "example_realtime",
            df,
            source=self.source,
            target_table="stock_realtime_market",
        )
        return True
