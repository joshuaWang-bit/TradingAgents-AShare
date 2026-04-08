"""Smart Cache Data Source - On-demand loading with LRU eviction.

This data source implements a smart caching strategy:
1. Load data on-demand when agent requests it (lazy loading)
2. LRU eviction when cache is full
3. Tiered storage: Hot (memory) -> Warm (SQLite) -> Cold (AkShare)
4. Storage quota management

Configuration:
    TA_CACHE_MAX_SYMBOLS=1000        # Max symbols to cache
    TA_CACHE_MAX_DAYS_PER_SYMBOL=90  # Max days per symbol
    TA_CACHE_MAX_SIZE_MB=500         # Max cache size in MB
    TA_CACHE_TTL_HOURS=24            # How long to keep unused data
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..base import DataSource, DataSourceConfig, DataAvailability, DataFreshness

logger = logging.getLogger(__name__)


class LRUCache:
    """Thread-safe LRU Cache with size limit."""
    
    def __init__(self, max_size: int = 100):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None
    
    def put(self, key: str, value: Any) -> Optional[str]:
        """Add item to cache. Returns evicted key if any."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = value
                return None
            
            # Check if we need to evict
            evicted = None
            if len(self._cache) >= self._max_size:
                evicted = next(iter(self._cache))
                del self._cache[evicted]
            
            self._cache[key] = value
            return evicted
    
    def remove(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
    
    def keys(self) -> List[str]:
        with self._lock:
            return list(self._cache.keys())
    
    def stats(self) -> Dict[str, int]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0,
            }


class SmartCacheDataSource(DataSource):
    """Smart caching data source with on-demand loading and LRU eviction.
    
    Strategy:
    1. Agent requests data -> Check memory cache (hot)
    2. Not in memory -> Check SQLite (warm)
    3. Not in SQLite -> Fetch from AkShare (cold), cache in SQLite
    4. LRU eviction when cache limits reached
    """
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        super().__init__(config)
        
        # Configuration
        self.max_symbols = self.config.config.get("max_symbols",
                                                   int(os.getenv("TA_CACHE_MAX_SYMBOLS", "1000")))
        self.max_days_per_symbol = self.config.config.get("max_days_per_symbol",
                                                          int(os.getenv("TA_CACHE_MAX_DAYS_PER_SYMBOL", "90")))
        self.max_size_mb = self.config.config.get("max_size_mb",
                                                  int(os.getenv("TA_CACHE_MAX_SIZE_MB", "500")))
        self.cache_ttl_hours = self.config.config.get("ttl_hours",
                                                      int(os.getenv("TA_CACHE_TTL_HOURS", "24")))
        
        self.cache_dir = Path(self.config.config.get("cache_dir",
                                                      os.getenv("TA_DATA_CACHE_DIR", "./data_cache")))
        
        # Hot cache (in-memory LRU)
        self._hot_cache = LRUCache(max_size=min(100, self.max_symbols // 10))
        
        # Database
        self._db_path: Optional[Path] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        
        # Fallback source
        self._fallback_source = None
        
        # Access tracking for statistics
        self._access_stats: Dict[str, Dict] = {}
    
    @property
    def name(self) -> str:
        return "smart_cache"
    
    @property
    def display_name(self) -> str:
        return "智能缓存 (按需加载+LRU淘汰)"
    
    @property
    def description(self) -> str:
        return (f"按需加载数据，LRU淘汰策略，"
                f"最多缓存 {self.max_symbols} 只股票，"
                f"每只股票 {self.max_days_per_symbol} 天数据")
    
    @property
    def supports_preload(self) -> bool:
        return False  # We do lazy loading, not preload
    
    @property
    def supports_realtime(self) -> bool:
        return True
    
    def _do_initialize(self) -> bool:
        """Initialize the SQLite cache database."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = self.cache_dir / "smart_cache.db"
            
            self._init_schema()
            
            # Initialize fallback
            from .akshare_source import AkshareDataSource
            self._fallback_source = AkshareDataSource()
            self._fallback_source.initialize()
            
            # Start cleanup thread
            self._start_cleanup_thread()
            
            logger.info(f"[SmartCache] Initialized with max_symbols={self.max_symbols}, "
                       f"max_days={self.max_days_per_symbol}, max_mb={self.max_size_mb}")
            return True
            
        except Exception as e:
            logger.error(f"[SmartCache] Failed to initialize: {e}")
            self._last_error = str(e)
            return False
    
    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript(f"""
            -- Price data cache with access tracking
            CREATE TABLE IF NOT EXISTS price_data (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                PRIMARY KEY (symbol, date)
            );
            
            -- Symbol metadata and access tracking
            CREATE TABLE IF NOT EXISTS symbol_cache_meta (
                symbol TEXT PRIMARY KEY,
                first_cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                record_count INTEGER DEFAULT 0,
                data_source TEXT DEFAULT 'akshare',
                is_stale INTEGER DEFAULT 0
            );
            
            -- Cache statistics
            CREATE TABLE IF NOT EXISTS cache_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_symbols INTEGER DEFAULT 0,
                total_records INTEGER DEFAULT 0,
                cache_size_mb REAL DEFAULT 0,
                last_cleanup_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            INSERT OR IGNORE INTO cache_stats (id) VALUES (1);
            
            -- Indexes for efficient queries
            CREATE INDEX IF NOT EXISTS idx_price_symbol_date ON price_data(symbol, date);
            CREATE INDEX IF NOT EXISTS idx_price_accessed ON price_data(symbol, last_accessed_at);
            CREATE INDEX IF NOT EXISTS idx_meta_accessed ON symbol_cache_meta(last_accessed_at);
            CREATE INDEX IF NOT EXISTS idx_meta_count ON symbol_cache_meta(access_count DESC);
        """)
        conn.commit()
        conn.close()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection (thread-local)."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    # -------------------------------------------------------------------------
    # Core Data Retrieval (Lazy Loading with Caching)
    # -------------------------------------------------------------------------
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get stock data with smart caching.
        
        Flow:
        1. Check hot cache (memory)
        2. Check warm cache (SQLite)
        3. Fetch from cold source (AkShare) if needed
        4. Cache result
        """
        cache_key = f"{symbol}:{start_date}:{end_date}"
        
        # 1. Check hot cache
        hot_data = self._hot_cache.get(cache_key)
        if hot_data:
            logger.debug(f"[SmartCache] Hot cache hit: {symbol}")
            return hot_data
        
        # 2. Check warm cache (SQLite)
        warm_data = self._get_from_warm_cache(symbol, start_date, end_date)
        if warm_data:
            logger.debug(f"[SmartCache] Warm cache hit: {symbol}")
            # Promote to hot cache
            self._hot_cache.put(cache_key, warm_data)
            self._update_access_stats(symbol)
            return warm_data
        
        # 3. Fetch from cold source (AkShare)
        logger.info(f"[SmartCache] Cache miss, fetching from AkShare: {symbol}")
        
        if not self._fallback_source:
            return f"No data available for {symbol}"
        
        try:
            # Fetch extended range for better caching (e.g., fetch 90 days even if only need 30)
            fetch_start = self._calculate_fetch_start(start_date)
            cold_data = self._fallback_source.get_stock_data(symbol, fetch_start, end_date)
            
            # 4. Cache the data
            self._cache_data(symbol, cold_data, end_date)
            
            # 5. Check cache limits and evict if needed
            self._enforce_cache_limits()
            
            # 6. Return requested range
            return self._filter_data_by_date(cold_data, start_date, end_date)
            
        except Exception as e:
            logger.error(f"[SmartCache] Failed to fetch {symbol}: {e}")
            return f"Failed to fetch data for {symbol}: {e}"
    
    def _calculate_fetch_start(self, requested_start: str) -> str:
        """Calculate how much data to fetch for optimal caching."""
        # Fetch more than requested to reduce future cache misses
        requested_dt = datetime.strptime(requested_start, "%Y-%m-%d")
        fetch_dt = requested_dt - timedelta(days=self.max_days_per_symbol - 30)
        return fetch_dt.strftime("%Y-%m-%d")
    
    def _get_from_warm_cache(self, symbol: str, start_date: str, end_date: str) -> Optional[str]:
        """Get data from SQLite cache."""
        try:
            with self._lock:
                conn = self._get_connection()
                
                # Check if we have data for this date range
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume 
                    FROM price_data 
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (symbol, start_date, end_date)
                ).fetchall()
                
                if not rows:
                    return None
                
                # Update access time
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE price_data 
                    SET last_accessed_at = ?, access_count = access_count + 1
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    """,
                    (now, symbol, start_date, end_date)
                )
                conn.execute(
                    """
                    UPDATE symbol_cache_meta 
                    SET last_accessed_at = ?, access_count = access_count + 1
                    WHERE symbol = ?
                    """,
                    (now, symbol)
                )
                conn.commit()
                
                # Format as CSV
                return self._rows_to_csv(symbol, start_date, end_date, rows)
                
        except Exception as e:
            logger.error(f"[SmartCache] Failed to read from warm cache: {e}")
            return None
    
    def _cache_data(self, symbol: str, csv_data: str, trade_date: str) -> int:
        """Parse and cache CSV data to SQLite."""
        import csv
        import io
        
        lines = csv_data.strip().split('\n')
        data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
        
        if len(data_lines) <= 1:
            return 0
        
        records = []
        reader = csv.DictReader(data_lines)
        for row in reader:
            try:
                date = row.get('Date', row.get('date', ''))
                if not date:
                    continue
                records.append((
                    symbol,
                    date,
                    float(row.get('Open', row.get('open', 0)) or 0),
                    float(row.get('High', row.get('high', 0)) or 0),
                    float(row.get('Low', row.get('low', 0)) or 0),
                    float(row.get('Close', row.get('close', 0)) or 0),
                    float(row.get('Volume', row.get('volume', 0)) or 0),
                ))
            except (ValueError, KeyError):
                continue
        
        if not records:
            return 0
        
        now = datetime.now(timezone.utc).isoformat()
        
        with self._lock:
            conn = self._get_connection()
            
            # Insert price data
            conn.executemany(
                """
                INSERT OR REPLACE INTO price_data 
                (symbol, date, open, high, low, close, volume, last_accessed_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], now) for r in records]
            )
            
            # Update symbol metadata
            conn.execute(
                """
                INSERT OR REPLACE INTO symbol_cache_meta 
                (symbol, last_accessed_at, access_count, record_count)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    last_accessed_at = excluded.last_accessed_at,
                    access_count = access_count + 1,
                    record_count = excluded.record_count
                """,
                (symbol, now, len(records))
            )
            
            conn.commit()
        
        logger.info(f"[SmartCache] Cached {len(records)} records for {symbol}")
        return len(records)
    
    def _enforce_cache_limits(self) -> None:
        """Enforce cache size limits using LRU eviction."""
        with self._lock:
            conn = self._get_connection()
            
            # Check symbol count limit
            count_row = conn.execute(
                "SELECT COUNT(*) as count FROM symbol_cache_meta"
            ).fetchone()
            
            if count_row and count_row['count'] > self.max_symbols:
                # Evict least recently used symbols
                to_evict = conn.execute(
                    """
                    SELECT symbol FROM symbol_cache_meta
                    ORDER BY last_accessed_at ASC, access_count ASC
                    LIMIT ?
                    """,
                    (count_row['count'] - self.max_symbols + 100,)  # Evict extra to avoid frequent cleanup
                ).fetchall()
                
                for row in to_evict:
                    self._evict_symbol(row['symbol'])
                
                logger.info(f"[SmartCache] Evicted {len(to_evict)} symbols due to limit")
            
            # Check storage size limit
            size_row = conn.execute(
                "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
            ).fetchone()
            
            if size_row and size_row['size'] > self.max_size_mb * 1024 * 1024:
                # Evict symbols until under limit
                self._evict_by_size(size_row['size'])
    
    def _evict_symbol(self, symbol: str) -> None:
        """Evict a symbol from cache."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM price_data WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM symbol_cache_meta WHERE symbol = ?", (symbol,))
            conn.commit()
            
            # Also remove from hot cache
            for key in list(self._hot_cache.keys()):
                if key.startswith(f"{symbol}:"):
                    self._hot_cache.remove(key)
        
        logger.debug(f"[SmartCache] Evicted symbol: {symbol}")
    
    def _evict_by_size(self, current_size: int) -> None:
        """Evict symbols to get under size limit."""
        target_size = self.max_size_mb * 1024 * 1024 * 0.8  # Target 80% of limit
        
        with self._lock:
            conn = self._get_connection()
            
            while current_size > target_size:
                # Get least recently used symbol
                row = conn.execute(
                    """
                    SELECT symbol FROM symbol_cache_meta
                    ORDER BY last_accessed_at ASC, access_count ASC
                    LIMIT 1
                    """
                ).fetchone()
                
                if not row:
                    break
                
                self._evict_symbol(row['symbol'])
                
                # Recalculate size
                size_row = conn.execute(
                    "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
                ).fetchone()
                current_size = size_row['size'] if size_row else 0
    
    def _rows_to_csv(self, symbol: str, start_date: str, end_date: str, rows: List[sqlite3.Row]) -> str:
        """Convert database rows to CSV format."""
        lines = ["Date,Open,High,Low,Close,Volume"]
        for row in rows:
            lines.append(f"{row['date']},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}")
        
        header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(rows)}\n"
        header += f"# Data source: smart_cache\n\n"
        
        return header + "\n".join(lines)
    
    def _filter_data_by_date(self, csv_data: str, start_date: str, end_date: str) -> str:
        """Filter CSV data to requested date range."""
        import csv
        import io
        
        lines = csv_data.strip().split('\n')
        header_lines = [l for l in lines if l.startswith('#')]
        data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
        
        if len(data_lines) <= 1:
            return csv_data
        
        filtered = [data_lines[0]]  # Header
        reader = csv.DictReader(data_lines)
        for row in reader:
            date = row.get('Date', row.get('date', ''))
            if start_date <= date <= end_date:
                filtered.append(f"{date},{row.get('Open', row.get('open', ''))},"
                              f"{row.get('High', row.get('high', ''))},"
                              f"{row.get('Low', row.get('low', ''))},"
                              f"{row.get('Close', row.get('close', ''))},"
                              f"{row.get('Volume', row.get('volume', ''))}")
        
        return "\n".join(header_lines + [""] + filtered)
    
    def _update_access_stats(self, symbol: str) -> None:
        """Update in-memory access statistics."""
        now = time.time()
        if symbol not in self._access_stats:
            self._access_stats[symbol] = {"count": 0, "first_access": now}
        self._access_stats[symbol]["count"] += 1
        self._access_stats[symbol]["last_access"] = now
    
    # -------------------------------------------------------------------------
    # Data Management Methods
    # -------------------------------------------------------------------------
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        """Check data availability."""
        try:
            from tradingagents.dataflows.trade_calendar import cn_today_str
            
            if trade_date is None:
                trade_date = cn_today_str()
            
            with self._lock:
                conn = self._get_connection()
                
                # Check metadata
                meta = conn.execute(
                    "SELECT * FROM symbol_cache_meta WHERE symbol = ?",
                    (symbol,)
                ).fetchone()
                
                if meta is None:
                    return DataAvailability(
                        symbol=symbol,
                        trade_date=trade_date,
                        freshness=DataFreshness.MISSING,
                        has_price_data=False,
                    )
                
                # Check freshness
                last_accessed = datetime.fromisoformat(meta['last_accessed_at'].replace('Z', '+00:00'))
                age_hours = (datetime.now(timezone.utc) - last_accessed).total_seconds() / 3600
                
                if age_hours > self.cache_ttl_hours:
                    freshness = DataFreshness.STALE
                else:
                    freshness = DataFreshness.FRESH
                
                return DataAvailability(
                    symbol=symbol,
                    trade_date=trade_date,
                    freshness=freshness,
                    has_price_data=True,
                    record_count=meta['record_count'],
                    last_updated=last_accessed,
                    metadata={
                        "access_count": meta['access_count'],
                        "first_cached": meta['first_cached_at'],
                    }
                )
                
        except Exception as e:
            logger.error(f"[SmartCache] Failed to check availability: {e}")
            return DataAvailability(
                symbol=symbol,
                trade_date=trade_date or "",
                freshness=DataFreshness.MISSING,
                has_price_data=False,
            )
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """Get detailed cache statistics."""
        try:
            with self._lock:
                conn = self._get_connection()
                
                # Basic counts
                symbol_count = conn.execute(
                    "SELECT COUNT(*) as count FROM symbol_cache_meta"
                ).fetchone()['count']
                
                record_count = conn.execute(
                    "SELECT COUNT(*) as count FROM price_data"
                ).fetchone()['count']
                
                # Storage size
                size_row = conn.execute(
                    "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
                ).fetchone()
                size_mb = (size_row['size'] / (1024 * 1024)) if size_row else 0
                
                # Most accessed symbols
                hot_symbols = conn.execute(
                    """
                    SELECT symbol, access_count, last_accessed_at 
                    FROM symbol_cache_meta
                    ORDER BY access_count DESC
                    LIMIT 10
                    """
                ).fetchall()
                
                return {
                    "total_symbols": symbol_count,
                    "total_records": record_count,
                    "cache_size_mb": round(size_mb, 2),
                    "max_symbols": self.max_symbols,
                    "max_size_mb": self.max_size_mb,
                    "utilization_percent": round(symbol_count / self.max_symbols * 100, 1),
                    "storage_percent": round(size_mb / self.max_size_mb * 100, 1),
                    "hot_cache_stats": self._hot_cache.stats(),
                    "top_symbols": [
                        {"symbol": r['symbol'], "access_count": r['access_count']}
                        for r in hot_symbols
                    ],
                }
        except Exception as e:
            logger.error(f"[SmartCache] Failed to get statistics: {e}")
            return {"error": str(e)}
    
    def cleanup_stale_data(self) -> Dict[str, int]:
        """Clean up stale data that's not been accessed recently."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.cache_ttl_hours)).isoformat()
        
        with self._lock:
            conn = self._get_connection()
            
            # Find stale symbols
            stale = conn.execute(
                "SELECT symbol FROM symbol_cache_meta WHERE last_accessed_at < ?",
                (cutoff,)
            ).fetchall()
            
            evicted_count = 0
            for row in stale:
                self._evict_symbol(row['symbol'])
                evicted_count += 1
            
            # Update stats
            conn.execute(
                """
                UPDATE cache_stats 
                SET last_cleanup_at = ?, updated_at = ?
                WHERE id = 1
                """,
                (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
        
        logger.info(f"[SmartCache] Cleaned up {evicted_count} stale symbols")
        return {"evicted_symbols": evicted_count}
    
    def _start_cleanup_thread(self) -> None:
        """Start background cleanup thread."""
        def cleanup_loop():
            while True:
                time.sleep(3600)  # Run every hour
                try:
                    self.cleanup_stale_data()
                except Exception as e:
                    logger.error(f"[SmartCache] Cleanup failed: {e}")
        
        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()
        logger.info("[SmartCache] Started cleanup thread")
    
    # -------------------------------------------------------------------------
    # Other Data Methods (delegated to fallback)
    # -------------------------------------------------------------------------
    
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get fundamentals - not cached, always fetch fresh."""
        if self._fallback_source:
            return self._fallback_source.get_fundamentals(ticker, curr_date)
        return f"Fundamentals not available"
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news - not cached, always fetch fresh."""
        if self._fallback_source:
            return self._fallback_source.get_news(ticker, start_date, end_date)
        return f"News not available"
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        """Get real-time quotes - always fetch fresh."""
        if self._fallback_source:
            return self._fallback_source.get_realtime_quotes(symbols)
        import json
        return json.dumps({})
