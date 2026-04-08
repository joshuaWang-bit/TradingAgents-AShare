"""Data source plugin system for TradingAgents.

This module provides a plugin architecture for supporting multiple data sources:
- akshare: Default real-time data from AkShare API
- preloaded: Daily pre-loaded data from AkShare to local SQLite
- xbx: Local data from XBX database (E:\STOCKDATA or similar)
- Custom user plugins

Usage:
    # In configuration
    DATA_SOURCE_PLUGIN = "preloaded"  # or "akshare", "xbx"
    
    # Register custom plugin
    from tradingagents.dataflows.plugins import DataSourceRegistry
    DataSourceRegistry.register("my_source", MyCustomDataSource)
"""

from .base import DataSource, DataSourceConfig, DataAvailability, DataFreshness
from .registry import DataSourceRegistry, get_data_source

__all__ = [
    "DataSource",
    "DataSourceConfig",
    "DataAvailability",
    "DataFreshness",
    "DataSourceRegistry",
    "get_data_source",
]
