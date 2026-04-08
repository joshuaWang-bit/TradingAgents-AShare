"""Smart Cache Data Source - On-demand loading with LRU eviction and multi-type caching.

This data source implements a smart caching strategy:
1. Load data on-demand when agent requests it (lazy loading)
2. LRU eviction when cache is full
3. Tiered storage: Hot (memory) -> Warm (SQLite) -> Cold (AkShare)
4. Multi-type caching with different TTLs:
   - Price data: 24 hours
   - Fundamentals: 7 days
   - Market sentiment (LHB, ZT pool): Intraday (until market close)
5. Storage quota management

Configuration:
    TA_CACHE_MAX_SYMBOLS=1000        # Max symbols to cache
    TA_CACHE_MAX_DAYS_PER_SYMBOL=90  # Max days per symbol for price data
    TA_CACHE_MAX_SIZE_MB=500         # Max cache size in MB
    
    # TTL configurations
    TA_CACHE_TTL_PRICE_HOURS=24           # Price data TTL
    TA_CACHE_TTL_FUNDAMENTALS_DAYS=7      # Fundamentals TTL
    TA_CACHE_TTL_SENTIMENT_HOURS=12       # Market sentiment TTL (intraday)
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Callable

from ..base import DataSource, DataSourceConfig, DataAvailability, DataFreshness

logger = logging.getLogger(__name__)


class CacheEntryType(Enum):
    """Types of cache entries with different TTL strategies."""
    PRICE = "price"                    # OHLCV data
    FUNDAMENTALS = "fundamentals"      # Company fundamentals
    FINANCIAL_REPORT = "financial"     # Balance sheet, cashflow, income
    NEWS = "news"                      # News data
    MARKET_SENTIMENT = "sentiment"     # LHB, ZT pool, fund flow
    INSIDER = "insider"                # Insider transactions


@dataclass
class TTLConfig:
    """TTL configuration for different data types."""
    price_hours: int = 24
    fundamentals_days: int = 7
    financial_days: int = 30           # Quarterly reports don't change often
    sentiment_hours: int = 12          # Intraday data
    news_hours: int = 6                # News gets stale quickly
    insider_days: int = 3              # Insider transactions
    
    def get_ttl_seconds(self, entry_type: CacheEntryType) -> int:
        """Get TTL in seconds for a given entry type."""
        ttl_map = {
            CacheEntryType.PRICE: self.price_hours * 3600,
            CacheEntryType.FUNDAMENTALS: self.fundamentals_days * 86400,
            CacheEntryType.FINANCIAL_REPORT: self.financial_days * 86400,
            CacheEntryType.NEWS: self.news_hours * 3600,
            CacheEntryType.MARKET_SENTIMENT: self.sentiment_hours * 3600,
            CacheEntryType.INSIDER: self.insider_days * 86400,
        }
        return ttl_map.get(entry_type, 3600)  # Default 1 hour


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
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None
    
    def put(self, key: str, value: Any) -> Optional[str]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = value
                return None
            
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
    """Smart caching data source with on-demand loading, LRU eviction, and multi-type caching."""
    
    def __init__(self, config: Optional[DataSourceConfig] = None):
        super().__init__(config)
        
        # Configuration
        self.max_symbols = self.config.config.get("max_symbols",
                                                   int(os.getenv("TA_CACHE_MAX_SYMBOLS", "1000")))
        self.max_days_per_symbol = self.config.config.get("max_days_per_symbol",
                                                          int(os.getenv("TA_CACHE_MAX_DAYS_PER_SYMBOL", "90")))
        self.max_size_mb = self.config.config.get("max_size_mb",
                                                  int(os.getenv("TA_CACHE_MAX_SIZE_MB", "500")))
        
        # TTL configuration
        self.ttl_config = TTLConfig(
            price_hours=self.config.config.get("ttl_price_hours",
                                               int(os.getenv("TA_CACHE_TTL_PRICE_HOURS", "24"))),
            fundamentals_days=self.config.config.get("ttl_fundamentals_days",
                                                      int(os.getenv("TA_CACHE_TTL_FUNDAMENTALS_DAYS", "7"))),
            financial_days=self.config.config.get("ttl_financial_days",
                                                   int(os.getenv("TA_CACHE_TTL_FINANCIAL_DAYS", "30"))),
            sentiment_hours=self.config.config.get("ttl_sentiment_hours",
                                                   int(os.getenv("TA_CACHE_TTL_SENTIMENT_HOURS", "12"))),
            news_hours=self.config.config.get("ttl_news_hours",
                                              int(os.getenv("TA_CACHE_TTL_NEWS_HOURS", "6"))),
            insider_days=self.config.config.get("ttl_insider_days",
                                                int(os.getenv("TA_CACHE_TTL_INSIDER_DAYS", "3"))),
        )
        
        self.cache_dir = Path(self.config.config.get("cache_dir",
                                                      os.getenv("TA_DATA_CACHE_DIR", "./data_cache")))
        
        # Hot caches for different data types
        self._price_cache = LRUCache(max_size=min(100, self.max_symbols // 10))
        self._fundamentals_cache = LRUCache(max_size=50)
        self._sentiment_cache = LRUCache(max_size=20)
        
        # Database
        self._db_path: Optional[Path] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        
        # Fallback source
        self._fallback_source = None
        
        # Access tracking
        self._access_stats: Dict[str, Dict] = {}
    
    @property
    def name(self) -> str:
        return "smart_cache"
    
    @property
    def display_name(self) -> str:
        return "智能缓存 (多类型数据+分层TTL)"
    
    @property
    def description(self) -> str:
        return (f"按需加载，LRU淘汰，多类型数据分层缓存:\n"
                f"- 价格数据: {self.ttl_config.price_hours}h TTL\n"
                f"- 基本面: {self.ttl_config.fundamentals_days}d TTL\n"
                f"- 市场情绪: {self.ttl_config.sentiment_hours}h TTL")
    
    @property
    def supports_preload(self) -> bool:
        return False
    
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
            
            logger.info(f"[SmartCache] Initialized: max_symbols={self.max_symbols}, "
                       f"max_mb={self.max_size_mb}, price_ttl={self.ttl_config.price_hours}h, "
                       f"fundamentals_ttl={self.ttl_config.fundamentals_days}d")
            return True
            
        except Exception as e:
            logger.error(f"[SmartCache] Failed to initialize: {e}")
            self._last_error = str(e)
            return False
    
    def _init_schema(self) -> None:
        """Initialize database schema for multi-type caching."""
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript(f"""
            -- Price data cache
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
            
            -- Text data cache (fundamentals, news, etc.)
            CREATE TABLE IF NOT EXISTS text_cache (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT NOT NULL,  -- 'fundamentals', 'news', 'sentiment', 'insider'
                symbol TEXT,
                trade_date TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                data_size INTEGER DEFAULT 0
            );
            
            -- Symbol metadata for price data
            CREATE TABLE IF NOT EXISTS symbol_cache_meta (
                symbol TEXT PRIMARY KEY,
                first_cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1,
                record_count INTEGER DEFAULT 0
            );
            
            -- Cache statistics
            CREATE TABLE IF NOT EXISTS cache_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_symbols INTEGER DEFAULT 0,
                total_records INTEGER DEFAULT 0,
                total_text_entries INTEGER DEFAULT 0,
                cache_size_mb REAL DEFAULT 0,
                last_cleanup_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            INSERT OR IGNORE INTO cache_stats (id) VALUES (1);
            
            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_price_symbol_date ON price_data(symbol, date);
            CREATE INDEX IF NOT EXISTS idx_price_accessed ON price_data(symbol, last_accessed_at);
            CREATE INDEX IF NOT EXISTS idx_meta_accessed ON symbol_cache_meta(last_accessed_at);
            CREATE INDEX IF NOT EXISTS idx_text_type ON text_cache(cache_type, symbol);
            CREATE INDEX IF NOT EXISTS idx_text_expires ON text_cache(expires_at);
            CREATE INDEX IF NOT EXISTS idx_text_accessed ON text_cache(last_accessed_at);
        """)
        conn.commit()
        conn.close()
    
    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    # -------------------------------------------------------------------------
    # Core Data Retrieval with Caching
    # -------------------------------------------------------------------------
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        """Get stock price data with smart caching."""
        cache_key = f"{symbol}:{start_date}:{end_date}"
        
        # Check hot cache
        hot_data = self._price_cache.get(cache_key)
        if hot_data:
            return hot_data
        
        # Check warm cache
        warm_data = self._get_price_from_db(symbol, start_date, end_date)
        if warm_data:
            self._price_cache.put(cache_key, warm_data)
            return warm_data
        
        # Fetch from source
        logger.info(f"[SmartCache] Price cache miss: {symbol}")
        if not self._fallback_source:
            return f"No data available for {symbol}"
        
        try:
            fetch_start = self._calculate_fetch_start(start_date)
            cold_data = self._fallback_source.get_stock_data(symbol, fetch_start, end_date)
            self._cache_price_data(symbol, cold_data, end_date)
            self._enforce_cache_limits()
            return self._filter_data_by_date(cold_data, start_date, end_date)
        except Exception as e:
            logger.error(f"[SmartCache] Failed to fetch price for {symbol}: {e}")
            return f"Failed to fetch data for {symbol}: {e}"
    
    def get_fundamentals(self, ticker: str, curr_date: Optional[str] = None) -> str:
        """Get fundamentals with caching (7-day TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.FUNDAMENTALS,
            symbol=ticker,
            trade_date=curr_date,
            fetch_func=lambda: self._fallback_source.get_fundamentals(ticker, curr_date),
            hot_cache=self._fundamentals_cache
        )
    
    def get_balance_sheet(self, ticker: str, freq: str = "quarterly", 
                         curr_date: Optional[str] = None) -> str:
        """Get balance sheet with caching (30-day TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.FINANCIAL_REPORT,
            symbol=ticker,
            trade_date=curr_date,
            fetch_func=lambda: self._fallback_source.get_balance_sheet(ticker, freq, curr_date),
            hot_cache=self._fundamentals_cache
        )
    
    def get_cashflow(self, ticker: str, freq: str = "quarterly",
                    curr_date: Optional[str] = None) -> str:
        """Get cashflow with caching (30-day TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.FINANCIAL_REPORT,
            symbol=ticker,
            trade_date=curr_date,
            fetch_func=lambda: self._fallback_source.get_cashflow(ticker, freq, curr_date),
            hot_cache=self._fundamentals_cache
        )
    
    def get_income_statement(self, ticker: str, freq: str = "quarterly",
                            curr_date: Optional[str] = None) -> str:
        """Get income statement with caching (30-day TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.FINANCIAL_REPORT,
            symbol=ticker,
            trade_date=curr_date,
            fetch_func=lambda: self._fallback_source.get_income_statement(ticker, freq, curr_date),
            hot_cache=self._fundamentals_cache
        )
    
    def get_lhb_detail(self, symbol: str, date: str) -> str:
        """Get 龙虎榜 with caching (intraday TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.MARKET_SENTIMENT,
            symbol=symbol,
            trade_date=date,
            fetch_func=lambda: self._fallback_source.get_lhb_detail(symbol, date),
            hot_cache=self._sentiment_cache
        )
    
    def get_zt_pool(self, date: str) -> str:
        """Get 涨停池 with caching (intraday TTL)."""
        # Use special symbol "_MARKET_" for market-wide data
        return self._get_text_cached(
            cache_type=CacheEntryType.MARKET_SENTIMENT,
            symbol="_MARKET_",
            trade_date=date,
            fetch_func=lambda: self._fallback_source.get_zt_pool(date),
            hot_cache=self._sentiment_cache,
            custom_key=f"zt_pool:{date}"
        )
    
    def get_board_fund_flow(self) -> str:
        """Get 板块资金流向 with caching (intraday TTL)."""
        from tradingagents.dataflows.trade_calendar import cn_today_str
        today = cn_today_str()
        return self._get_text_cached(
            cache_type=CacheEntryType.MARKET_SENTIMENT,
            symbol="_MARKET_",
            trade_date=today,
            fetch_func=lambda: self._fallback_source.get_board_fund_flow(),
            hot_cache=self._sentiment_cache,
            custom_key=f"board_fund_flow:{today}"
        )
    
    def get_individual_fund_flow(self, symbol: str) -> str:
        """Get 个股资金流向 with caching (intraday TTL)."""
        from tradingagents.dataflows.trade_calendar import cn_today_str
        today = cn_today_str()
        return self._get_text_cached(
            cache_type=CacheEntryType.MARKET_SENTIMENT,
            symbol=symbol,
            trade_date=today,
            fetch_func=lambda: self._fallback_source.get_individual_fund_flow(symbol),
            hot_cache=self._sentiment_cache,
            custom_key=f"fund_flow:{symbol}:{today}"
        )
    
    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        """Get news with caching (short TTL - 6 hours)."""
        cache_key = f"news:{ticker}:{start_date}:{end_date}"
        return self._get_text_cached(
            cache_type=CacheEntryType.NEWS,
            symbol=ticker,
            trade_date=end_date,
            fetch_func=lambda: self._fallback_source.get_news(ticker, start_date, end_date),
            custom_key=cache_key
        )
    
    def get_insider_transactions(self, ticker: str) -> str:
        """Get insider transactions with caching (3-day TTL)."""
        return self._get_text_cached(
            cache_type=CacheEntryType.INSIDER,
            symbol=ticker,
            trade_date=None,
            fetch_func=lambda: self._fallback_source.get_insider_transactions(ticker),
            custom_key=f"insider:{ticker}"
        )
    
    # -------------------------------------------------------------------------
    # Generic Text Caching Implementation
    # -------------------------------------------------------------------------
    
    def _get_text_cached(
        self,
        cache_type: CacheEntryType,
        symbol: Optional[str],
        trade_date: Optional[str],
        fetch_func: Callable[[], str],
        hot_cache: Optional[LRUCache] = None,
        custom_key: Optional[str] = None
    ) -> str:
        """Generic text data caching with TTL."""
        cache_key = custom_key or f"{cache_type.value}:{symbol}:{trade_date}"
        
        # Check hot cache
        if hot_cache:
            hot_data = hot_cache.get(cache_key)
            if hot_data:
                return hot_data
        
        # Check database cache
        cached_data = self._get_text_from_db(cache_key, cache_type)
        if cached_data:
            if hot_cache:
                hot_cache.put(cache_key, cached_data)
            return cached_data
        
        # Fetch from source
        logger.info(f"[SmartCache] {cache_type.value} cache miss: {symbol}")
        if not self._fallback_source:
            return f"No {cache_type.value} data available"
        
        try:
            data = fetch_func()
            self._cache_text_data(cache_key, cache_type, symbol, trade_date, data)
            if hot_cache:
                hot_cache.put(cache_key, data)
            return data
        except Exception as e:
            logger.error(f"[SmartCache] Failed to fetch {cache_type.value} for {symbol}: {e}")
            return f"Failed to fetch {cache_type.value} data: {e}"
    
    def _get_text_from_db(self, cache_key: str, 
                         cache_type: CacheEntryType) -> Optional[str]:
        """Get text data from database cache."""
        try:
            with self._lock:
                conn = self._get_connection()
                
                row = conn.execute(
                    """
                    SELECT data FROM text_cache 
                    WHERE cache_key = ? AND cache_type = ? AND expires_at > datetime('now')
                    """,
                    (cache_key, cache_type.value)
                ).fetchone()
                
                if row:
                    # Update access stats
                    conn.execute(
                        """
                        UPDATE text_cache 
                        SET last_accessed_at = datetime('now'), 
                            access_count = access_count + 1
                        WHERE cache_key = ?
                        """,
                        (cache_key,)
                    )
                    conn.commit()
                    return row['data']
                
                return None
        except Exception as e:
            logger.error(f"[SmartCache] Failed to read text cache: {e}")
            return None
    
    def _cache_text_data(self, cache_key: str, cache_type: CacheEntryType,
                        symbol: Optional[str], trade_date: Optional[str],
                        data: str) -> None:
        """Cache text data with TTL."""
        ttl_seconds = self.ttl_config.get_ttl_seconds(cache_type)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        data_size = len(data.encode('utf-8'))
        
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT OR REPLACE INTO text_cache 
                (cache_key, cache_type, symbol, trade_date, data, expires_at, data_size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cache_key, cache_type.value, symbol, trade_date, data, expires_at, data_size)
            )
            conn.commit()
        
        logger.debug(f"[SmartCache] Cached {cache_type.value} for {symbol}, size={data_size}, ttl={ttl_seconds}s")
    
    # -------------------------------------------------------------------------
    # Price Data Caching
    # -------------------------------------------------------------------------
    
    def _calculate_fetch_start(self, requested_start: str) -> str:
        requested_dt = datetime.strptime(requested_start, "%Y-%m-%d")
        fetch_dt = requested_dt - timedelta(days=self.max_days_per_symbol - 30)
        return fetch_dt.strftime("%Y-%m-%d")
    
    def _get_price_from_db(self, symbol: str, start_date: str, end_date: str) -> Optional[str]:
        try:
            with self._lock:
                conn = self._get_connection()
                
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
                
                return self._rows_to_csv(symbol, start_date, end_date, rows)
        except Exception as e:
            logger.error(f"[SmartCache] Failed to read price cache: {e}")
            return None
    
    def _cache_price_data(self, symbol: str, csv_data: str, trade_date: str) -> int:
        import csv
        
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
                    symbol, date,
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
            conn.executemany(
                """
                INSERT OR REPLACE INTO price_data 
                (symbol, date, open, high, low, close, volume, last_accessed_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], now) for r in records]
            )
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
        
        logger.info(f"[SmartCache] Cached {len(records)} price records for {symbol}")
        return len(records)
    
    def _rows_to_csv(self, symbol: str, start_date: str, end_date: str, 
                     rows: List[sqlite3.Row]) -> str:
        lines = ["Date,Open,High,Low,Close,Volume"]
        for row in rows:
            lines.append(f"{row['date']},{row['open']},{row['high']},{row['low']},{row['close']},{row['volume']}")
        
        header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(rows)}\n"
        header += f"# Data source: smart_cache\n\n"
        
        return header + "\n".join(lines)
    
    def _filter_data_by_date(self, csv_data: str, start_date: str, end_date: str) -> str:
        import csv
        
        lines = csv_data.strip().split('\n')
        header_lines = [l for l in lines if l.startswith('#')]
        data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
        
        if len(data_lines) <= 1:
            return csv_data
        
        filtered = [data_lines[0]]
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
    
    # -------------------------------------------------------------------------
    # Cache Management
    # -------------------------------------------------------------------------
    
    def _enforce_cache_limits(self) -> None:
        with self._lock:
            conn = self._get_connection()
            
            # Check price data symbol count
            count_row = conn.execute(
                "SELECT COUNT(*) as count FROM symbol_cache_meta"
            ).fetchone()
            
            if count_row and count_row['count'] > self.max_symbols:
                to_evict = conn.execute(
                    """
                    SELECT symbol FROM symbol_cache_meta
                    ORDER BY last_accessed_at ASC, access_count ASC
                    LIMIT ?
                    """,
                    (count_row['count'] - self.max_symbols + 100,)
                ).fetchall()
                
                for row in to_evict:
                    self._evict_symbol(row['symbol'])
                
                logger.info(f"[SmartCache] Evicted {len(to_evict)} symbols")
            
            # Check storage size
            size_row = conn.execute(
                "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
            ).fetchone()
            
            if size_row and size_row['size'] > self.max_size_mb * 1024 * 1024:
                self._evict_by_size(size_row['size'])
    
    def _evict_symbol(self, symbol: str) -> None:
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM price_data WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM symbol_cache_meta WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM text_cache WHERE symbol = ?", (symbol,))
            conn.commit()
            
            for key in list(self._price_cache.keys()):
                if key.startswith(f"{symbol}:"):
                    self._price_cache.remove(key)
        
        logger.debug(f"[SmartCache] Evicted symbol: {symbol}")
    
    def _evict_by_size(self, current_size: int) -> None:
        target_size = self.max_size_mb * 1024 * 1024 * 0.8
        
        with self._lock:
            conn = self._get_connection()
            
            while current_size > target_size:
                # Evict oldest price data first
                row = conn.execute(
                    "SELECT symbol FROM symbol_cache_meta ORDER BY last_accessed_at ASC LIMIT 1"
                ).fetchone()
                
                if not row:
                    # No more price data, evict text cache
                    text_row = conn.execute(
                        "SELECT cache_key FROM text_cache ORDER BY last_accessed_at ASC LIMIT 1"
                    ).fetchone()
                    if text_row:
                        conn.execute("DELETE FROM text_cache WHERE cache_key = ?", (text_row['cache_key'],))
                        conn.commit()
                    else:
                        break
                else:
                    self._evict_symbol(row['symbol'])
                
                size_row = conn.execute(
                    "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
                ).fetchone()
                current_size = size_row['size'] if size_row else 0
    
    def cleanup_expired(self) -> Dict[str, int]:
        """Clean up expired entries."""
        with self._lock:
            conn = self._get_connection()
            
            # Clean expired text cache
            result = conn.execute(
                "DELETE FROM text_cache WHERE expires_at < datetime('now')"
            )
            expired_text = result.rowcount
            
            # Clean old price data (based on TTL)
            price_ttl = self.ttl_config.price_hours
            result = conn.execute(
                """
                DELETE FROM price_data 
                WHERE last_accessed_at < datetime('now', '-{} hours')
                """.format(price_ttl)
            )
            expired_price = result.rowcount
            
            # Clean orphaned symbols
            conn.execute(
                """
                DELETE FROM symbol_cache_meta 
                WHERE symbol NOT IN (SELECT DISTINCT symbol FROM price_data)
                """
            )
            
            conn.commit()
        
        total = expired_text + expired_price
        if total > 0:
            logger.info(f"[SmartCache] Cleaned up {expired_text} text + {expired_price} price entries")
        
        return {"expired_text": expired_text, "expired_price": expired_price}
    
    def _start_cleanup_thread(self) -> None:
        def cleanup_loop():
            while True:
                time.sleep(3600)  # Run every hour
                try:
                    self.cleanup_expired()
                except Exception as e:
                    logger.error(f"[SmartCache] Cleanup failed: {e}")
        
        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()
        logger.info("[SmartCache] Started cleanup thread")
    
    # -------------------------------------------------------------------------
    # Other Methods (pass-through)
    # -------------------------------------------------------------------------
    
    def get_indicators(self, symbol: str, indicator: str, curr_date: str, 
                      look_back_days: int) -> str:
        if self._fallback_source:
            return self._fallback_source.get_indicators(symbol, indicator, curr_date, look_back_days)
        return f"Indicators not available"
    
    def get_realtime_quotes(self, symbols: List[str]) -> str:
        if self._fallback_source:
            return self._fallback_source.get_realtime_quotes(symbols)
        import json
        return json.dumps({})
    
    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        if self._fallback_source:
            return self._fallback_source.get_global_news(curr_date, look_back_days, limit)
        return "Global news not available"
    
    def get_hot_stocks_xq(self) -> str:
        if self._fallback_source:
            return self._fallback_source.get_hot_stocks_xq()
        return "Hot stocks not available"
    
    # -------------------------------------------------------------------------
    # Status and Statistics
    # -------------------------------------------------------------------------
    
    def check_availability(self, symbol: str, trade_date: Optional[str] = None) -> DataAvailability:
        try:
            from tradingagents.dataflows.trade_calendar import cn_today_str
            
            if trade_date is None:
                trade_date = cn_today_str()
            
            with self._lock:
                conn = self._get_connection()
                
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
                
                last_accessed = datetime.fromisoformat(meta['last_accessed_at'].replace('Z', '+00:00'))
                age_hours = (datetime.now(timezone.utc) - last_accessed).total_seconds() / 3600
                
                freshness = DataFreshness.FRESH if age_hours < self.ttl_config.price_hours else DataFreshness.STALE
                
                return DataAvailability(
                    symbol=symbol,
                    trade_date=trade_date,
                    freshness=freshness,
                    has_price_data=True,
                    has_fundamentals=self._has_text_cached(symbol, CacheEntryType.FUNDAMENTALS),
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
    
    def _has_text_cached(self, symbol: str, cache_type: CacheEntryType) -> bool:
        """Check if text data is cached."""
        try:
            conn = self._get_connection()
            row = conn.execute(
                """
                SELECT 1 FROM text_cache 
                WHERE symbol = ? AND cache_type = ? AND expires_at > datetime('now')
                LIMIT 1
                """,
                (symbol, cache_type.value)
            ).fetchone()
            return row is not None
        except:
            return False
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        try:
            with self._lock:
                conn = self._get_connection()
                
                # Price data stats
                symbol_count = conn.execute(
                    "SELECT COUNT(*) as count FROM symbol_cache_meta"
                ).fetchone()['count']
                
                record_count = conn.execute(
                    "SELECT COUNT(*) as count FROM price_data"
                ).fetchone()['count']
                
                # Text cache stats by type
                text_stats = conn.execute(
                    """
                    SELECT cache_type, COUNT(*) as count, SUM(data_size) as total_size
                    FROM text_cache WHERE expires_at > datetime('now')
                    GROUP BY cache_type
                    """
                ).fetchall()
                
                text_by_type = {
                    row['cache_type']: {
                        "count": row['count'],
                        "size_kb": round(row['total_size'] / 1024, 2) if row['total_size'] else 0
                    }
                    for row in text_stats
                }
                
                # Storage size
                size_row = conn.execute(
                    "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
                ).fetchone()
                size_mb = (size_row['size'] / (1024 * 1024)) if size_row else 0
                
                # Hot symbols
                hot_symbols = conn.execute(
                    """
                    SELECT symbol, access_count FROM symbol_cache_meta
                    ORDER BY access_count DESC LIMIT 10
                    """
                ).fetchall()
                
                return {
                    "price_data": {
                        "symbols": symbol_count,
                        "records": record_count,
                    },
                    "text_cache": text_by_type,
                    "storage_mb": round(size_mb, 2),
                    "max_mb": self.max_size_mb,
                    "utilization_percent": round(size_mb / self.max_size_mb * 100, 1),
                    "hot_cache_stats": {
                        "price": self._price_cache.stats(),
                        "fundamentals": self._fundamentals_cache.stats(),
                        "sentiment": self._sentiment_cache.stats(),
                    },
                    "hot_symbols": [{"symbol": r['symbol'], "access": r['access_count']} for r in hot_symbols],
                    "ttl_config": {
                        "price_hours": self.ttl_config.price_hours,
                        "fundamentals_days": self.ttl_config.fundamentals_days,
                        "sentiment_hours": self.ttl_config.sentiment_hours,
                    }
                }
        except Exception as e:
            logger.error(f"[SmartCache] Failed to get statistics: {e}")
            return {"error": str(e)}
