"""AkShare data source - default real-time data from AkShare API."""

import logging
from typing import Any, Dict, List, Optional

from ..base import DataSource, DataSourceConfig, DataAvailability, DataFreshness

logger = logging.getLogger(__name__)


class AkshareDataSource(DataSource):
    """Data source using AkShare API for real-time data.
    
    This is the default data source that fetches data directly from
    AkShare API on each request.
    """
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        super().__init__(config)
        self._provider = None
    
    @property
    def name(self) -> str:
        return "akshare"
    
    @property
    def display_name(self) -> str:
        return "AkShare 实时数据"
    
    @property
    def description(self) -> str:
        return "直接从 AkShare API 获取实时数据，每次请求都会访问网络"
    
    @property
    def supports_preload(self) -> bool:
        return False  # Real-time source doesn't support preloading
    
    @property
    def supports_realtime(self) -> bool:
        return True
    
    def _do_initialize(self) -> bool:
        """Initialize the AkShare provider."""
        try:
            from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
            self._provider = CnAkshareProvider()
            logger.info("[AkshareDataSource] Initialized successfully")
            return True
        except Exception as e:
            logger.error(f"[AkshareDataSource] Failed to initialize: {e}")
            self._last_error = str(e)
            return False
    
    def _ensure_provider(self):
        """Ensure provider is initialized."""
        if self._provider is None:
            self._do_initialize()
        if self._provider is None:
            raise RuntimeError("AkShare provider not available")
        return self._provider
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get stock price data from AkShare."""
        provider = self._ensure_provider()
        return provider.get_stock_data(symbol, start_date, end_date)
    
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get company fundamentals from AkShare."""
        provider = self._ensure_provider()
        return provider.get_fundamentals(ticker, curr_date)
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news from AkShare."""
        provider = self._ensure_provider()
        return provider.get_news(ticker, start_date, end_date)
    
    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        """Get global news from AkShare."""
        provider = self._ensure_provider()
        return provider.get_global_news(curr_date, look_back_days, limit)
    
    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        """Get technical indicators from AkShare."""
        provider = self._ensure_provider()
        return provider.get_indicators(symbol, indicator, curr_date, look_back_days)
    
    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get balance sheet from AkShare."""
        provider = self._ensure_provider()
        return provider.get_balance_sheet(ticker, freq, curr_date)
    
    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get cash flow from AkShare."""
        provider = self._ensure_provider()
        return provider.get_cashflow(ticker, freq, curr_date)
    
    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get income statement from AkShare."""
        provider = self._ensure_provider()
        return provider.get_income_statement(ticker, freq, curr_date)
    
    def get_insider_transactions(self, symbol: str) -> str:
        """Get insider transactions from AkShare."""
        provider = self._ensure_provider()
        return provider.get_insider_transactions(symbol)
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        """Get real-time quotes from AkShare."""
        provider = self._ensure_provider()
        return provider.get_realtime_quotes(symbols)
    
    def get_board_fund_flow(self) -> str:
        """Get board fund flow from AkShare."""
        provider = self._ensure_provider()
        return provider.get_board_fund_flow()
    
    def get_individual_fund_flow(self, symbol: str) -> str:
        """Get individual fund flow from AkShare."""
        provider = self._ensure_provider()
        return provider.get_individual_fund_flow(symbol)
    
    def get_lhb_detail(self, symbol: str, date: str) -> str:
        """Get龙虎榜 details from AkShare."""
        provider = self._ensure_provider()
        return provider.get_lhb_detail(symbol, date)
    
    def get_zt_pool(self, date: str) -> str:
        """Get涨停池 from AkShare."""
        provider = self._ensure_provider()
        return provider.get_zt_pool(date)
    
    def get_hot_stocks_xq(self) -> str:
        """Get hot stocks from XueQiu via AkShare."""
        provider = self._ensure_provider()
        return provider.get_hot_stocks_xq()
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        """Check availability - for AkShare, always returns FRESH if accessible."""
        is_available = self.is_available()
        return DataAvailability(
            symbol=symbol,
            trade_date=trade_date or "",
            freshness=DataFreshness.FRESH if is_available else DataFreshness.MISSING,
            has_price_data=is_available,
            metadata={"source": "real-time api"},
        )
