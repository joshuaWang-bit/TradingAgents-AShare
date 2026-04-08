"""Dataflow interface with plugin-based data source support.

This module provides routing to data sources with fallback support.
It now supports the new plugin-based data source architecture while
maintaining backward compatibility.
"""

import os
import logging
from typing import Optional, List, Dict, Any

from .alpha_vantage_common import AlphaVantageRateLimitError
from .config import get_config
from .providers import build_default_registry

logger = logging.getLogger(__name__)

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": ["get_stock_data"],
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": ["get_indicators"],
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
        ],
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ],
    },
    "realtime_data": {
        "description": "Real-time market quotes",
        "tools": ["get_realtime_quotes"],
    },
    "cn_market_data": {
        "description": "China A-share market sentiment and fund flow data",
        "tools": [
            "get_board_fund_flow",
            "get_individual_fund_flow",
            "get_lhb_detail",
            "get_zt_pool",
            "get_hot_stocks_xq",
        ],
    },
}

# Legacy registry (for backward compatibility)
_registry = build_default_registry()
VENDOR_LIST = _registry.list_names()

# New plugin system - initialized lazily
_plugin_initialized = False
_data_source = None


def _ensure_plugins_initialized():
    """Initialize plugin system on first use."""
    global _plugin_initialized, _data_source
    if not _plugin_initialized:
        from .plugins.loader import initialize_plugins
        from .plugins.registry import get_data_source
        initialize_plugins()
        _data_source = get_data_source()
        _plugin_initialized = True


def get_current_data_source():
    """Get the currently active data source (plugin-based).
    
    This returns the data source based on TA_DATA_SOURCE environment variable
    or the default (akshare).
    """
    _ensure_plugins_initialized()
    return _data_source


def _is_trace_enabled() -> bool:
    env_value = os.getenv("TA_TRACE")
    if env_value is not None:
        return env_value.strip().lower() in ("1", "true", "yes", "on")

    config = get_config()
    return bool(config.get("provider_trace", True))


def _trace(msg: str) -> None:
    if _is_trace_enabled():
        print(f"[provider-trace] {msg}", flush=True)


def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")


def get_vendor(category: str, method: str = None) -> str:
    """Get configured vendor for category or tool method."""
    config = get_config()

    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    return config.get("data_vendors", {}).get(category, "yfinance")


def _resolve_vendor_chain(method: str, configured_vendor: str) -> list[str]:
    configured = [v.strip() for v in configured_vendor.split(",") if v.strip()]
    fallback = configured.copy()

    for provider_name in _registry.list_names():
        if provider_name not in fallback:
            fallback.append(provider_name)

    return fallback


def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to provider implementations with fallback support.
    
    This function now supports both the legacy provider system and the new
    plugin-based data sources. If a plugin-based source is configured, it
    will be used; otherwise falls back to legacy providers.
    """
    # Try new plugin system first if configured
    data_source = get_current_data_source()
    
    if data_source is not None and hasattr(data_source, method):
        try:
            func = getattr(data_source, method)
            _trace(f"method={method} source={data_source.name} status=hit (plugin)")
            return func(*args, **kwargs)
        except (AlphaVantageRateLimitError, NotImplementedError) as exc:
            _trace(f"method={method} source={data_source.name} status=fallback reason={type(exc).__name__}")
            # Fall through to legacy system
        except Exception as exc:
            _trace(f"method={method} source={data_source.name} status=fallback reason={type(exc).__name__}")
            # Fall through to legacy system
    
    # Legacy provider system
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    fallback_vendors = _resolve_vendor_chain(method, vendor_config)
    last_exc = None
    _trace(
        f"method={method} category={category} configured='{vendor_config}' "
        f"chain={fallback_vendors}"
    )

    for vendor in fallback_vendors:
        provider = _registry.get(vendor)
        if provider is None:
            _trace(f"method={method} vendor={vendor} status=skip reason=not-registered")
            continue

        impl_func = getattr(provider, method, None)
        if impl_func is None:
            _trace(f"method={method} vendor={vendor} status=skip reason=not-implemented")
            continue

        try:
            result = impl_func(*args, **kwargs)
            _trace(f"method={method} vendor={vendor} status=hit")
            return result
        except (AlphaVantageRateLimitError, NotImplementedError) as exc:
            last_exc = exc
            # Try next provider for transient/routing issues or placeholder providers.
            _trace(
                f"method={method} vendor={vendor} status=fallback "
                f"reason={type(exc).__name__}"
            )
            continue
        except Exception as exc:
            # Provider-specific runtime/parsing errors (e.g., schema changes, KeyError)
            # should not terminate the full chain; fall through to next vendor.
            last_exc = exc
            _trace(
                f"method={method} vendor={vendor} status=fallback "
                f"reason={type(exc).__name__}"
            )
            continue

    _trace(f"method={method} status=failed reason=no-available-vendor")
    if last_exc is not None:
        raise RuntimeError(
            f"No available vendor for method '{method}'. "
            f"Configured chain: {fallback_vendors}. "
            f"Last error: {type(last_exc).__name__}: {last_exc}"
        ) from last_exc
    raise RuntimeError(
        f"No available vendor for method '{method}'. "
        f"Configured chain: {fallback_vendors}"
    )


# =============================================================================
# New Plugin-Based API
# =============================================================================

def check_data_availability(symbol: str, trade_date: Optional[str] = None) -> Dict[str, Any]:
    """Check data availability for a symbol using the current data source.
    
    Args:
        symbol: Stock symbol
        trade_date: Trade date to check (optional)
        
    Returns:
        Dict with availability information
    """
    data_source = get_current_data_source()
    if data_source is None:
        return {
            "symbol": symbol,
            "available": False,
            "error": "No data source available",
        }
    
    availability = data_source.check_availability(symbol, trade_date)
    return {
        "symbol": availability.symbol,
        "trade_date": availability.trade_date,
        "freshness": availability.freshness.value,
        "has_price_data": availability.has_price_data,
        "has_fundamentals": availability.has_fundamentals,
        "has_news": availability.has_news,
        "has_fund_flow": availability.has_fund_flow,
        "record_count": availability.record_count,
        "last_updated": availability.last_updated.isoformat() if availability.last_updated else None,
        "ready_for_analysis": availability.is_ready_for_analysis(),
    }


def get_data_source_info() -> Dict[str, Any]:
    """Get information about the current data source.
    
    Returns:
        Dict with data source information
    """
    _ensure_plugins_initialized()
    from .plugins.registry import DataSourceRegistry
    
    return {
        "current_source": _data_source.name if _data_source else None,
        "available_sources": DataSourceRegistry.list_source_info(),
        "health": DataSourceRegistry.health_check() if _plugin_initialized else {},
    }


def preload_data(symbols: List[str], trade_date: str) -> Dict[str, Any]:
    """Trigger data preloading for specified symbols.
    
    This only works with data sources that support preloading (e.g., preloaded).
    
    Args:
        symbols: List of stock symbols to preload
        trade_date: Trade date to preload for
        
    Returns:
        Dict with preload operation results
    """
    data_source = get_current_data_source()
    if data_source is None:
        return {"success": False, "error": "No data source available"}
    
    if not data_source.supports_preload:
        return {
            "success": False,
            "error": f"Data source '{data_source.name}' does not support preloading",
        }
    
    try:
        return data_source.preload_data(symbols, trade_date)
    except Exception as e:
        logger.error(f"[interface] Preload failed: {e}")
        return {"success": False, "error": str(e)}


def get_preload_status() -> Dict[str, Any]:
    """Get status of preloaded data.
    
    Returns:
        Dict with preload status
    """
    data_source = get_current_data_source()
    if data_source is None:
        return {"error": "No data source available"}
    
    return data_source.get_preload_status()
