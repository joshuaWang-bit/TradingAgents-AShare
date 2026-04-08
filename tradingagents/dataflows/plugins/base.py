"""Base classes for data source plugins.

This module defines the abstract interface that all data sources must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
import logging

logger = logging.getLogger(__name__)


class DataFreshness(Enum):
    """Data freshness levels."""
    FRESH = "fresh"           # Data is current and complete
    STALE = "stale"           # Data is old but usable
    MISSING = "missing"       # Data is not available
    LOADING = "loading"       # Data is currently being loaded
    PARTIAL = "partial"       # Data is partially available


@dataclass
class DataAvailability:
    """Represents the availability status of data for a symbol."""
    symbol: str
    trade_date: str
    freshness: DataFreshness
    has_price_data: bool = False
    has_fundamentals: bool = False
    has_news: bool = False
    has_fund_flow: bool = False
    record_count: int = 0
    last_updated: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_ready_for_analysis(self, min_records: int = 30) -> bool:
        """Check if data is sufficient for analysis."""
        if self.freshness in (DataFreshness.LOADING, DataFreshness.MISSING):
            return False
        return self.has_price_data and self.record_count >= min_records


@dataclass
class DataSourceConfig:
    """Configuration for a data source plugin."""
    name: str
    enabled: bool = True
    priority: int = 100  # Lower = higher priority
    config: Dict[str, Any] = field(default_factory=dict)
    fallback_to_realtime: bool = True  # Fall back to real-time if cached data unavailable


class DataSource(ABC):
    """Abstract base class for all data sources.
    
    All data source plugins must inherit from this class and implement
    the required abstract methods.
    
    Example:
        class MyDataSource(DataSource):
            @property
            def name(self) -> str:
                return "my_source"
            
            def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
                # Implementation here
                pass
    """
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        self.config = config or DataSourceConfig(name=self.name)
        self._initialized = False
        self._last_error: Optional[str] = None
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique name of this data source.
        
        This name is used to identify and select the data source.
        Must be unique across all plugins.
        """
        pass
    
    @property
    def display_name(self) -> str:
        """Return human-readable name for UI display."""
        return self.name
    
    @property
    def description(self) -> str:
        """Return description of this data source."""
        return ""
    
    @property
    def supports_preload(self) -> bool:
        """Whether this data source supports pre-loading data.
        
        Data sources like xbx or preloaded SQLite should return True.
        Real-time sources like akshare should return False.
        """
        return False
    
    @property
    def supports_realtime(self) -> bool:
        """Whether this data source supports real-time data fetching."""
        return True
    
    def initialize(self) -> bool:
        """Initialize the data source.
        
        Called once when the data source is first used.
        Returns True if initialization successful.
        """
        try:
            self._initialized = self._do_initialize()
            return self._initialized
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[{self.name}] Initialization failed: {e}")
            return False
    
    @abstractmethod
    def _do_initialize(self) -> bool:
        """Actual initialization logic. Override in subclass."""
        return True
    
    def is_available(self) -> bool:
        """Check if this data source is available for use."""
        if not self._initialized:
            self.initialize()
        return self._initialized
    
    # -------------------------------------------------------------------------
    # Data Retrieval Methods - All data sources must implement these
    # -------------------------------------------------------------------------
    
    @abstractmethod
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get OHLCV stock price data.
        
        Args:
            symbol: Stock symbol (e.g., "000001" for 平安银行)
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            CSV-formatted string with OHLCV data
        """
        pass
    
    @abstractmethod
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get company fundamentals data.
        
        Args:
            ticker: Stock ticker symbol
            curr_date: Current date for context
            
        Returns:
            Markdown-formatted fundamentals report
        """
        pass
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news for a specific ticker.
        
        Default implementation returns "not supported".
        Override if the data source provides news data.
        """
        return f"News data not supported by {self.name}"
    
    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        """Get global market news.
        
        Default implementation returns "not supported".
        """
        return f"Global news not supported by {self.name}"
    
    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        """Get technical indicators.
        
        Default implementation computes from price data.
        Override for optimized indicator retrieval.
        """
        return f"Indicators not directly supported by {self.name}"
    
    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get balance sheet data."""
        return f"Balance sheet not supported by {self.name}"
    
    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get cash flow data."""
        return f"Cash flow not supported by {self.name}"
    
    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: Optional[str] = None
    ) -> str:
        """Get income statement data."""
        return f"Income statement not supported by {self.name}"
    
    def get_insider_transactions(self, symbol: str) -> str:
        """Get insider transaction data."""
        return f"Insider transactions not supported by {self.name}"
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        """Get real-time quotes for symbols.
        
        Returns JSON-formatted string.
        """
        import json
        return json.dumps({})
    
    def get_board_fund_flow(self) -> str:
        """Get board/sector fund flow data."""
        return f"Board fund flow not supported by {self.name}"
    
    def get_individual_fund_flow(self, symbol: str) -> str:
        """Get individual stock fund flow data."""
        return f"Individual fund flow not supported by {self.name}"
    
    def get_lhb_detail(self, symbol: str, date: str) -> str:
        """Get龙虎榜 (dragon tiger board) details."""
        return f"LHB data not supported by {self.name}"
    
    def get_zt_pool(self, date: str) -> str:
        """Get涨停池 (limit-up pool) data."""
        return f"ZT pool not supported by {self.name}"
    
    def get_hot_stocks_xq(self) -> str:
        """Get hot stocks from XueQiu."""
        return f"Hot stocks not supported by {self.name}"
    
    # -------------------------------------------------------------------------
    # Data Management Methods - For preload-capable sources
    # -------------------------------------------------------------------------
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        """Check data availability for a symbol.
        
        Default implementation assumes data is available if get_stock_data works.
        Override for more precise availability checking.
        """
        try:
            # Try to get a small window of data to verify availability
            from datetime import timedelta
            
            if trade_date is None:
                from tradingagents.dataflows.trade_calendar import cn_today_str
                trade_date = cn_today_str()
            
            end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=30)
            start_date = start_dt.strftime("%Y-%m-%d")
            
            data = self.get_stock_data(symbol, start_date, trade_date)
            has_data = len(data) > 100 if data else False
            
            return DataAvailability(
                symbol=symbol,
                trade_date=trade_date,
                freshness=DataFreshness.FRESH if has_data else DataFreshness.MISSING,
                has_price_data=has_data,
                record_count=30 if has_data else 0,
            )
        except Exception as e:
            return DataAvailability(
                symbol=symbol,
                trade_date=trade_date or "",
                freshness=DataFreshness.MISSING,
                has_price_data=False,
            )
    
    def preload_data(self, symbols: List[str], trade_date: str, 
                     progress_callback: Optional[Callable[[str, bool], None]] = None) -> Dict[str, Any]:
        """Pre-load data for specified symbols.
        
        Only called if supports_preload returns True.
        
        Args:
            symbols: List of symbols to preload
            trade_date: Trade date to preload for
            progress_callback: Optional callback(symbol, success) for progress updates
            
        Returns:
            Dict with statistics about the preload operation
        """
        raise NotImplementedError(f"{self.name} does not support data preloading")
    
    def get_preload_status(self) -> Dict[str, Any]:
        """Get status of preloaded data.
        
        Returns:
            Dict with preload status information
        """
        return {
            "supported": self.supports_preload,
            "available": False,
            "message": "Preloading not supported by this data source",
        }
    
    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------
    
    def get_last_error(self) -> Optional[str]:
        """Get the last error message, if any."""
        return self._last_error
    
    def clear_error(self) -> None:
        """Clear the last error."""
        self._last_error = None
    
    def health_check(self) -> Dict[str, Any]:
        """Perform a health check on this data source.
        
        Returns:
            Dict with health status information
        """
        return {
            "name": self.name,
            "initialized": self._initialized,
            "available": self.is_available(),
            "last_error": self._last_error,
            "supports_preload": self.supports_preload,
            "supports_realtime": self.supports_realtime,
        }
