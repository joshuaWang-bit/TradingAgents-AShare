"""Preloaded data source - loads data daily from AkShare to local SQLite.

This data source:
1. Preloads data from AkShare daily (scheduled)
2. Serves data from local SQLite cache
3. Falls back to AkShare if local data unavailable
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..base import DataSource, DataSourceConfig, DataAvailability, DataFreshness

logger = logging.getLogger(__name__)


class PreloadedDataSource(DataSource):
    """Data source using pre-loaded local SQLite data.
    
    Data is preloaded daily from AkShare and stored in a local SQLite database.
    This provides fast local access and reduces API calls.
    
    Configuration:
        TA_DATA_CACHE_DIR: Directory for cache database (default: ./data_cache)
        TA_DATA_MAX_AGE_HOURS: Max age before data considered stale (default: 24)
        TA_DATA_FALLBACK_TO_REALTIME: Fall back to AkShare if cache miss (default: true)
    """
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        super().__init__(config)
        self._db_path: Optional[Path] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._fallback_source: Optional[DataSource] = None
        
        # Configuration
        self.cache_dir = Path(self.config.config.get("cache_dir", 
                                                      os.getenv("TA_DATA_CACHE_DIR", "./data_cache")))
        self.max_age_hours = self.config.config.get("max_age_hours",
                                                     int(os.getenv("TA_DATA_MAX_AGE_HOURS", "24")))
        self.fallback_to_realtime = self.config.config.get("fallback_to_realtime",
                                                           os.getenv("TA_DATA_FALLBACK_TO_REALTIME", "true").lower() == "true")
    
    @property
    def name(self) -> str:
        return "preloaded"
    
    @property
    def display_name(self) -> str:
        return "预加载本地数据"
    
    @property
    def description(self) -> str:
        return "从 AkShare 每日预加载数据到本地 SQLite，优先使用缓存，支持自动回退到实时获取"
    
    @property
    def supports_preload(self) -> bool:
        return True
    
    @property
    def supports_realtime(self) -> bool:
        return True  # Via fallback
    
    def _do_initialize(self) -> bool:
        """Initialize the SQLite database."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = self.cache_dir / "preloaded_data.db"
            
            # Initialize schema
            self._init_schema()
            
            # Initialize fallback source if enabled
            if self.fallback_to_realtime:
                from .akshare_source import AkshareDataSource
                self._fallback_source = AkshareDataSource()
                self._fallback_source.initialize()
            
            logger.info(f"[PreloadedDataSource] Initialized with cache at {self._db_path}")
            return True
        except Exception as e:
            logger.error(f"[PreloadedDataSource] Failed to initialize: {e}")
            self._last_error = str(e)
            return False
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection (thread-local)."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript("""
            -- Price data cache
            CREATE TABLE IF NOT EXISTS price_data (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, date)
            );
            
            -- Data availability tracking
            CREATE TABLE IF NOT EXISTS data_availability (
                symbol TEXT PRIMARY KEY,
                trade_date TEXT NOT NULL,
                has_price_data INTEGER DEFAULT 0,
                has_fundamentals INTEGER DEFAULT 0,
                has_news INTEGER DEFAULT 0,
                has_fund_flow INTEGER DEFAULT 0,
                record_count INTEGER DEFAULT 0,
                price_data_updated_at TIMESTAMP,
                fundamentals_updated_at TIMESTAMP,
                news_updated_at TIMESTAMP,
                fund_flow_updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Preload operation log
            CREATE TABLE IF NOT EXISTS preload_log (
                id TEXT PRIMARY KEY,
                trade_date TEXT NOT NULL,
                status TEXT NOT NULL,
                total_symbols INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                failed_symbols TEXT,  -- JSON array
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                duration_seconds REAL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- System state
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_price_symbol_date ON price_data(symbol, trade_date);
            CREATE INDEX IF NOT EXISTS idx_price_date ON price_data(date);
            CREATE INDEX IF NOT EXISTS idx_availability_date ON data_availability(trade_date, has_price_data);
            CREATE INDEX IF NOT EXISTS idx_preload_log_date ON preload_log(trade_date);
        """)
        conn.commit()
        conn.close()
    
    # -------------------------------------------------------------------------
    # Data Retrieval Methods
    # -------------------------------------------------------------------------
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get stock data from local cache or fallback."""
        # Try local cache first
        cached = self._get_cached_price_data(symbol, start_date, end_date)
        if cached:
            return cached
        
        # Fall back to real-time if enabled
        if self.fallback_to_realtime and self._fallback_source:
            logger.info(f"[PreloadedDataSource] Cache miss for {symbol}, falling back to real-time")
            return self._fallback_source.get_stock_data(symbol, start_date, end_date)
        
        return f"No data available for {symbol} from {start_date} to {end_date}"
    
    def _get_cached_price_data(self, symbol: str, start_date: str, end_date: str) -> Optional[str]:
        """Get price data from local cache."""
        try:
            with self._lock:
                conn = self._get_connection()
                cursor = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume 
                    FROM price_data 
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (symbol, start_date, end_date)
                )
                rows = cursor.fetchall()
                
                if not rows:
                    return None
                
                # Format as CSV
                lines = ["Date,Open,High,Low,Close,Volume"]
                for row in rows:
                    lines.append(f"{row['date']},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}")
                
                header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
                header += f"# Total records: {len(rows)}\n"
                header += f"# Data source: preloaded cache\n\n"
                
                return header + "\n".join(lines)
        except Exception as e:
            logger.error(f"[PreloadedDataSource] Failed to get cached data: {e}")
            return None
    
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get fundamentals - currently falls back to real-time."""
        if self.fallback_to_realtime and self._fallback_source:
            return self._fallback_source.get_fundamentals(ticker, curr_date)
        return f"Fundamentals not available in preloaded cache for {ticker}"
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news - currently falls back to real-time."""
        if self.fallback_to_realtime and self._fallback_source:
            return self._fallback_source.get_news(ticker, start_date, end_date)
        return f"News not available in preloaded cache for {ticker}"
    
    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        """Get global news - falls back to real-time."""
        if self.fallback_to_realtime and self._fallback_source:
            return self._fallback_source.get_global_news(curr_date, look_back_days, limit)
        return "Global news not available in preloaded cache"
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        """Get real-time quotes - always falls back to real-time source."""
        if self._fallback_source:
            return self._fallback_source.get_realtime_quotes(symbols)
        return json.dumps({})
    
    # -------------------------------------------------------------------------
    # Data Management Methods
    # -------------------------------------------------------------------------
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        """Check data availability from cache."""
        try:
            from tradingagents.dataflows.trade_calendar import cn_today_str
            
            if trade_date is None:
                trade_date = cn_today_str()
            
            with self._lock:
                conn = self._get_connection()
                row = conn.execute(
                    "SELECT * FROM data_availability WHERE symbol = ?",
                    (symbol,)
                ).fetchone()
                
                if row is None:
                    return DataAvailability(
                        symbol=symbol,
                        trade_date=trade_date,
                        freshness=DataFreshness.MISSING,
                        has_price_data=False,
                    )
                
                # Check freshness
                updated_at = row['price_data_updated_at']
                if updated_at:
                    updated_dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    age_hours = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600
                    freshness = DataFreshness.FRESH if age_hours < self.max_age_hours else DataFreshness.STALE
                else:
                    freshness = DataFreshness.MISSING
                
                return DataAvailability(
                    symbol=symbol,
                    trade_date=row['trade_date'],
                    freshness=freshness,
                    has_price_data=bool(row['has_price_data']),
                    has_fundamentals=bool(row['has_fundamentals']),
                    has_news=bool(row['has_news']),
                    has_fund_flow=bool(row['has_fund_flow']),
                    record_count=row['record_count'] or 0,
                    last_updated=datetime.fromisoformat(updated_at) if updated_at else None,
                )
        except Exception as e:
            logger.error(f"[PreloadedDataSource] Failed to check availability: {e}")
            return DataAvailability(
                symbol=symbol,
                trade_date=trade_date or "",
                freshness=DataFreshness.MISSING,
                has_price_data=False,
            )
    
    def preload_data(self, symbols: List[str], trade_date: str,
                     progress_callback: Optional[Callable[[str, bool], None]] = None) -> Dict[str, Any]:
        """Preload data for specified symbols from AkShare.
        
        This is the main method for daily data loading.
        """
        import uuid
        
        operation_id = uuid.uuid4().hex
        start_time = datetime.now(timezone.utc)
        
        logger.info(f"[PreloadedDataSource] Starting preload operation {operation_id} for {len(symbols)} symbols")
        
        # Log start
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO preload_log (id, trade_date, status, total_symbols, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (operation_id, trade_date, DataLoadStatus.RUNNING.value, len(symbols), start_time.isoformat())
            )
            conn.execute(
                "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
                ("preload_status", "running")
            )
            conn.commit()
        
        success_count = 0
        failed_count = 0
        skipped_count = 0
        failed_symbols: List[str] = []
        
        # Get AkShare source for fetching
        from .akshare_source import AkshareDataSource
        akshare = AkshareDataSource()
        if not akshare.initialize():
            error_msg = "Failed to initialize AkShare for preloading"
            self._finish_preload(operation_id, trade_date, False, error_msg, 
                                len(symbols), 0, 0, 0, [])
            return {"success": False, "error": error_msg}
        
        # Calculate date range (fetch 1 year of data for indicator calculation)
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=365)
        start_date = start_dt.strftime("%Y-%m-%d")
        
        for i, symbol in enumerate(symbols):
            try:
                # Fetch data from AkShare
                data = akshare.get_stock_data(symbol, start_date, trade_date)
                
                # Parse and store in database
                records_inserted = self._store_price_data(symbol, trade_date, data)
                
                if records_inserted > 0:
                    success_count += 1
                    # Update availability
                    self._update_availability(symbol, trade_date, records_inserted)
                else:
                    skipped_count += 1
                    
                if progress_callback:
                    progress_callback(symbol, records_inserted > 0)
                    
            except Exception as e:
                logger.error(f"[PreloadedDataSource] Failed to preload {symbol}: {e}")
                failed_count += 1
                failed_symbols.append(symbol)
                if progress_callback:
                    progress_callback(symbol, False)
            
            # Progress logging every 10 symbols
            if (i + 1) % 10 == 0:
                logger.info(f"[PreloadedDataSource] Progress: {i+1}/{len(symbols)} processed")
        
        # Finish
        success = failed_count == 0
        self._finish_preload(operation_id, trade_date, success, None,
                            len(symbols), success_count, failed_count, skipped_count, failed_symbols)
        
        return {
            "success": success,
            "operation_id": operation_id,
            "total": len(symbols),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "failed_symbols": failed_symbols,
        }
    
    def _store_price_data(self, symbol: str, trade_date: str, csv_data: str) -> int:
        """Parse CSV data and store in database. Returns number of records inserted."""
        import io
        import csv
        
        lines = csv_data.strip().split('\n')
        
        # Skip header lines starting with #
        data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
        
        if len(data_lines) <= 1:  # Header only or empty
            return 0
        
        records = []
        reader = csv.DictReader(data_lines)
        for row in reader:
            try:
                records.append((
                    symbol,
                    trade_date,
                    row.get('Date', row.get('date', '')),
                    float(row.get('Open', row.get('open', 0))),
                    float(row.get('High', row.get('high', 0))),
                    float(row.get('Low', row.get('low', 0))),
                    float(row.get('Close', row.get('close', 0))),
                    float(row.get('Volume', row.get('volume', 0))),
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"[PreloadedDataSource] Failed to parse row: {e}")
                continue
        
        if not records:
            return 0
        
        with self._lock:
            conn = self._get_connection()
            # Delete old data for this symbol
            conn.execute("DELETE FROM price_data WHERE symbol = ?", (symbol,))
            # Insert new data
            conn.executemany(
                """
                INSERT OR REPLACE INTO price_data 
                (symbol, trade_date, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records
            )
            conn.commit()
        
        return len(records)
    
    def _update_availability(self, symbol: str, trade_date: str, record_count: int) -> None:
        """Update data availability record."""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT OR REPLACE INTO data_availability 
                (symbol, trade_date, has_price_data, record_count, price_data_updated_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (symbol, trade_date, record_count, now, now)
            )
            conn.commit()
    
    def _finish_preload(self, operation_id: str, trade_date: str, success: bool, 
                       error_message: Optional[str], total: int, success_count: int,
                       failed_count: int, skipped_count: int, failed_symbols: List[str]) -> None:
        """Finalize preload operation."""
        end_time = datetime.now(timezone.utc)
        
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                UPDATE preload_log 
                SET status = ?, completed_at = ?, duration_seconds = ?,
                    success_count = ?, failed_count = ?, skipped_count = ?,
                    failed_symbols = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    DataLoadStatus.COMPLETED.value if success else DataLoadStatus.FAILED.value,
                    end_time.isoformat(),
                    None,  # duration calculated from timestamps
                    success_count, failed_count, skipped_count,
                    json.dumps(failed_symbols),
                    error_message,
                    operation_id
                )
            )
            conn.execute(
                "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
                ("preload_status", "completed" if success else "failed")
            )
            conn.commit()
        
        logger.info(f"[PreloadedDataSource] Preload {operation_id} finished: "
                   f"success={success}, total={total}, success={success_count}, failed={failed_count}")
    
    def get_preload_status(self) -> Dict[str, Any]:
        """Get status of preloaded data."""
        try:
            with self._lock:
                conn = self._get_connection()
                
                # Get latest preload log
                latest = conn.execute(
                    "SELECT * FROM preload_log ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                
                # Get count of available symbols
                count_row = conn.execute(
                    "SELECT COUNT(*) as count FROM data_availability WHERE has_price_data = 1"
                ).fetchone()
                
                # Get stale symbols count
                stale_threshold = (datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)).isoformat()
                stale_row = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM data_availability 
                    WHERE has_price_data = 1 AND price_data_updated_at < ?
                    """,
                    (stale_threshold,)
                ).fetchone()
                
                return {
                    "supported": True,
                    "available": True,
                    "cache_path": str(self._db_path),
                    "cached_symbols": count_row['count'] if count_row else 0,
                    "stale_symbols": stale_row['count'] if stale_row else 0,
                    "max_age_hours": self.max_age_hours,
                    "latest_preload": dict(latest) if latest else None,
                }
        except Exception as e:
            logger.error(f"[PreloadedDataSource] Failed to get preload status: {e}")
            return {
                "supported": True,
                "available": False,
                "error": str(e),
            }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get detailed statistics about cached data."""
        try:
            with self._lock:
                conn = self._get_connection()
                
                total = conn.execute(
                    "SELECT COUNT(*) as count FROM data_availability"
                ).fetchone()['count']
                
                fresh = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM data_availability 
                    WHERE has_price_data = 1 AND price_data_updated_at > datetime('now', '-{} hours')
                    """.format(self.max_age_hours)
                ).fetchone()['count']
                
                stale = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM data_availability 
                    WHERE has_price_data = 1 AND price_data_updated_at <= datetime('now', '-{} hours')
                    """.format(self.max_age_hours)
                ).fetchone()['count']
                
                missing = total - fresh - stale
                
                return {
                    "total_symbols": total,
                    "fresh": fresh,
                    "stale": stale,
                    "missing": missing,
                    "is_loading": False,  # Could check system_state
                }
        except Exception as e:
            logger.error(f"[PreloadedDataSource] Failed to get statistics: {e}")
            return {"error": str(e)}
