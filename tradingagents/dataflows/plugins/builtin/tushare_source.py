"""Tushare data source - A-share data from Tushare API.

This data source uses Tushare Pro API for market data.
Requires TUSHARE_TOKEN environment variable.

Usage:
    export TUSHARE_TOKEN=your_token_here
    export TA_DATA_SOURCE=tushare
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import DataSource, DataSourceConfig, DataAvailability, DataFreshness

logger = logging.getLogger(__name__)


class TushareDataSource(DataSource):
    """Data source using Tushare Pro API.
    
    Configuration:
        TUSHARE_TOKEN: Tushare API token (required)
        TUSHARE_RATE_LIMIT: Requests per minute (default: 500)
    """
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        super().__init__(config)
        self._token = os.getenv("TUSHARE_TOKEN")
        self._pro = None
        self._rate_limit = int(os.getenv("TUSHARE_RATE_LIMIT", "500"))
    
    @property
    def name(self) -> str:
        return "tushare"
    
    @property
    def display_name(self) -> str:
        return "Tushare Pro"
    
    @property
    def description(self) -> str:
        return "A-share data from Tushare Pro API"
    
    @property
    def supports_preload(self) -> bool:
        return False
    
    @property
    def supports_realtime(self) -> bool:
        return True
    
    def _do_initialize(self) -> bool:
        """Initialize Tushare API."""
        if not self._token:
            logger.error("[TushareDataSource] TUSHARE_TOKEN not set")
            self._last_error = "TUSHARE_TOKEN environment variable not set"
            return False
        
        try:
            import tushare as ts
            self._pro = ts.pro_api(self._token)
            logger.info("[TushareDataSource] Initialized successfully")
            return True
        except Exception as e:
            logger.error(f"[TushareDataSource] Failed to initialize: {e}")
            self._last_error = str(e)
            return False
    
    def _ensure_pro(self):
        """Ensure Tushare pro API is initialized."""
        if self._pro is None:
            self._do_initialize()
        if self._pro is None:
            raise RuntimeError("Tushare not initialized")
        return self._pro
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get stock price data from Tushare."""
        pro = self._ensure_pro()
        
        # Convert symbol to Tushare format (000001.SZ -> 000001)
        code = symbol.split('.')[0] if '.' in symbol else symbol
        
        # Determine exchange
        if code.startswith(('6', '5', '9')):
            exchange = 'SH'
        else:
            exchange = 'SZ'
        
        ts_code = f"{code}.{exchange}"
        
        try:
            df = pro.daily(ts_code=ts_code, start_date=start_date.replace('-', ''), 
                          end_date=end_date.replace('-', ''))
            
            if df is None or df.empty:
                return f"No data found for {symbol}"
            
            # Rename columns to standard format
            df = df.rename(columns={
                'trade_date': 'Date',
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'vol': 'Volume',
            })
            
            # Format date
            df['Date'] = df['Date'].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}")
            
            # Format as CSV
            header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
            header += f"# Total records: {len(df)}\n"
            header += f"# Data source: tushare\n\n"
            
            return header + df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].to_csv(index=False)
            
        except Exception as e:
            logger.error(f"[TushareDataSource] Failed to get stock data for {symbol}: {e}")
            raise
    
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get company fundamentals from Tushare."""
        pro = self._ensure_pro()
        
        code = ticker.split('.')[0] if '.' in ticker else ticker
        
        try:
            # Get stock company info
            df = pro.stock_company(ts_code=f"{code}.SZ" if code.startswith(('0', '3')) else f"{code}.SH")
            
            if df is None or df.empty:
                return f"No fundamentals data for {ticker}"
            
            # Format as markdown
            info = df.iloc[0]
            parts = [f"## Fundamentals for {ticker}"]
            parts.append(f"### Company Profile")
            parts.append(f"**Name**: {info.get('name', 'N/A')}")
            parts.append(f"**Industry**: {info.get('industry', 'N/A')}")
            parts.append(f"**Area**: {info.get('area', 'N/A')}")
            parts.append(f"**List Date**: {info.get('list_date', 'N/A')}")
            
            return "\n\n".join(parts)
            
        except Exception as e:
            logger.error(f"[TushareDataSource] Failed to get fundamentals: {e}")
            return f"Fundamentals not available: {e}"
    
    def get_daily_basic(self, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get daily basic data for all stocks (market snapshot).
        
        This is the key method for market snapshot functionality.
        
        Returns:
            List of dict with stock data including:
            - ts_code, name, close, change_pct, vol, amount
            - turnover_rate, pe, pb, total_mv, circ_mv
        """
        pro = self._ensure_pro()
        
        if trade_date is None:
            from tradingagents.dataflows.trade_calendar import cn_today_str
            trade_date = cn_today_str().replace('-', '')
        else:
            trade_date = trade_date.replace('-', '')
        
        try:
            # Get daily basic data
            df = pro.daily_basic(trade_date=trade_date)
            
            if df is None or df.empty:
                logger.warning(f"[TushareDataSource] No daily_basic data for {trade_date}")
                return []
            
            # Get stock names
            stock_basic = pro.stock_basic(exchange='', list_status='L')
            name_map = dict(zip(stock_basic['ts_code'], stock_basic['name']))
            
            # Merge names
            df['name'] = df['ts_code'].map(name_map)
            
            # Convert to list of dicts
            records = []
            for _, row in df.iterrows():
                records.append({
                    'ts_code': row['ts_code'],
                    'symbol': row['ts_code'].split('.')[0],
                    'name': row.get('name', ''),
                    'price': row.get('close', 0),
                    'change_pct': row.get('pct_chg', 0),
                    'volume': row.get('vol', 0),
                    'amount': row.get('amount', 0),
                    'turnover_rate': row.get('turnover_rate', 0),
                    'pe_ratio': row.get('pe', 0),
                    'pb_ratio': row.get('pb', 0),
                    'market_cap': row.get('circ_mv', 0) * 10000,  # Convert to yuan
                    'total_cap': row.get('total_mv', 0) * 10000,
                })
            
            logger.info(f"[TushareDataSource] Fetched {len(records)} stocks from daily_basic")
            return records
            
        except Exception as e:
            logger.error(f"[TushareDataSource] Failed to get daily basic: {e}")
            raise
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        """Get real-time quotes. Tushare doesn't support real-time well."""
        import json
        return json.dumps({})
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news from Tushare."""
        return f"News not supported by Tushare"
    
    def get_indicators(self, symbol: str, indicator: str, curr_date: str, 
                      look_back_days: int) -> str:
        """Get technical indicators."""
        return f"Indicators not directly supported by Tushare"
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        """Check data availability."""
        is_available = self._token is not None and self._pro is not None
        
        return DataAvailability(
            symbol=symbol,
            trade_date=trade_date or "",
            freshness=DataFreshness.FRESH if is_available else DataFreshness.MISSING,
            has_price_data=is_available,
            metadata={"source": "tushare", "token_set": self._token is not None},
        )
