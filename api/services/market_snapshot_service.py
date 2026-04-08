"""Market snapshot service for caching full market data after market close.

This service fetches all A-share stock quotes using ak.stock_zh_a_spot_em()
after market close (15:30) and caches them locally for fast access.

Usage:
    # Automatic daily fetch after market close
    # Manual trigger:
    from api.services.market_snapshot_service import get_snapshot_service
    service = get_snapshot_service()
    await service.fetch_and_cache_daily_snapshot()
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

try:
    import pandas as pd
except ImportError:
    pd = None

from api.database import get_db_ctx

logger = logging.getLogger(__name__)


class MarketSnapshotService:
    """Service for managing daily market snapshot caching."""
    
    def __init__(self):
        self._db_path: Optional[Path] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._is_fetching = False
        
        # Configuration
        self.cache_dir = Path(os.getenv("TA_DATA_CACHE_DIR", "./data_cache"))
        self.auto_fetch_enabled = os.getenv("TA_MARKET_SNAPSHOT_AUTO", "true").lower() == "true"
        self.fetch_time = os.getenv("TA_MARKET_SNAPSHOT_TIME", "15:35")  # 15:35 after market close
        
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database for market snapshots."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.cache_dir / "market_snapshot.db"
        
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                price REAL,
                change_pct REAL,
                change_amount REAL,
                volume REAL,
                amount REAL,
                amplitude REAL,
                high REAL,
                low REAL,
                open_price REAL,
                pre_close REAL,
                volume_ratio REAL,
                turnover_rate REAL,
                pe_ratio REAL,
                pb_ratio REAL,
                market_cap REAL,
                total_cap REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, symbol)
            );
            
            CREATE INDEX IF NOT EXISTS idx_snapshot_date ON daily_snapshots(trade_date);
            CREATE INDEX IF NOT EXISTS idx_snapshot_change ON daily_snapshots(trade_date, change_pct DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshot_amount ON daily_snapshots(trade_date, amount DESC);
            
            CREATE TABLE IF NOT EXISTS snapshot_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                status TEXT NOT NULL,
                total_stocks INTEGER,
                fetched_at TIMESTAMP,
                duration_seconds REAL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_log_date ON snapshot_log(trade_date);
        """)
        conn.commit()
        conn.close()
        
        logger.info(f"[MarketSnapshot] Database initialized at {self._db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    async def fetch_and_cache_daily_snapshot(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """Fetch and cache daily market snapshot from AkShare.
        
        Args:
            trade_date: Trade date (YYYY-MM-DD). If None, uses current trading day.
            
        Returns:
            Dict with fetch results
        """
        if self._is_fetching:
            return {"success": False, "error": "Already fetching snapshot"}
        
        self._is_fetching = True
        
        try:
            from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day
            
            if trade_date is None:
                trade_date = cn_today_str()
            
            # Check if it's a trading day
            if not is_cn_trading_day(trade_date):
                logger.info(f"[MarketSnapshot] Skipping non-trading day: {trade_date}")
                return {"success": True, "message": "Non-trading day", "trade_date": trade_date}
            
            # Check if already fetched today
            if self._is_snapshot_exists(trade_date):
                logger.info(f"[MarketSnapshot] Snapshot already exists for {trade_date}")
                return {"success": True, "message": "Already exists", "trade_date": trade_date}
            
            if pd is None:
                raise ImportError("pandas is required for market snapshot service")
            
            logger.info(f"[MarketSnapshot] Starting fetch for {trade_date}")
            start_time = datetime.now(timezone.utc)
            
            # Log start
            self._log_fetch_start(trade_date)
            
            # Fetch data from AkShare in thread pool
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, self._fetch_from_akshare)
            
            if df is None or df.empty:
                raise ValueError("No data returned from AkShare")
            
            # Cache to database
            record_count = await loop.run_in_executor(None, self._save_to_db, df, trade_date)
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            # Log success
            self._log_fetch_complete(trade_date, "success", record_count, duration)
            
            logger.info(f"[MarketSnapshot] Fetched {record_count} stocks in {duration:.1f}s")
            
            return {
                "success": True,
                "trade_date": trade_date,
                "total_stocks": record_count,
                "duration_seconds": duration,
            }
            
        except Exception as e:
            logger.error(f"[MarketSnapshot] Failed to fetch snapshot: {e}")
            self._log_fetch_complete(trade_date, "failed", 0, 0, str(e))
            return {"success": False, "error": str(e), "trade_date": trade_date}
        finally:
            self._is_fetching = False
    
    def _fetch_from_akshare(self) -> Any:
        """Fetch data from AkShare."""
        try:
            import akshare as ak
            
            logger.info("[MarketSnapshot] Fetching from AkShare stock_zh_a_spot_em...")
            df = ak.stock_zh_a_spot_em()
            
            # Rename columns to standard names
            column_map = {
                '代码': 'symbol',
                '名称': 'name',
                '最新价': 'price',
                '涨跌幅': 'change_pct',
                '涨跌额': 'change_amount',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '最高': 'high',
                '最低': 'low',
                '今开': 'open_price',
                '昨收': 'pre_close',
                '量比': 'volume_ratio',
                '换手率': 'turnover_rate',
                '市盈率-动态': 'pe_ratio',
                '市净率': 'pb_ratio',
                '流通市值': 'market_cap',
                '总市值': 'total_cap',
            }
            
            df = df.rename(columns=column_map)
            
            # Clean numeric columns
            numeric_cols = ['price', 'change_pct', 'change_amount', 'volume', 'amount',
                          'amplitude', 'high', 'low', 'open_price', 'pre_close',
                          'volume_ratio', 'turnover_rate', 'pe_ratio', 'pb_ratio',
                          'market_cap', 'total_cap']
            
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            return df
            
        except Exception as e:
            logger.error(f"[MarketSnapshot] AkShare fetch failed: {e}")
            raise
    
    def _save_to_db(self, df: Any, trade_date: str) -> int:
        """Save DataFrame to database."""
        # Prepare data
        df['trade_date'] = trade_date
        
        # Select only columns we need
        columns = ['trade_date', 'symbol', 'name', 'price', 'change_pct', 'change_amount',
                   'volume', 'amount', 'amplitude', 'high', 'low', 'open_price', 'pre_close',
                   'volume_ratio', 'turnover_rate', 'pe_ratio', 'pb_ratio', 'market_cap', 'total_cap']
        
        df_to_save = df[[col for col in columns if col in df.columns]].copy()
        
        with self._lock:
            conn = self._get_connection()
            
            # Clear existing data for this date
            conn.execute("DELETE FROM daily_snapshots WHERE trade_date = ?", (trade_date,))
            
            # Insert new data
            df_to_save.to_sql('daily_snapshots', conn, if_exists='append', index=False)
            
            conn.commit()
        
        return len(df_to_save)
    
    def _is_snapshot_exists(self, trade_date: str) -> bool:
        """Check if snapshot already exists for date."""
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT 1 FROM daily_snapshots WHERE trade_date = ? LIMIT 1",
                (trade_date,)
            ).fetchone()
            return row is not None
        except:
            return False
    
    def _log_fetch_start(self, trade_date: str) -> None:
        """Log fetch start."""
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO snapshot_log (trade_date, status) VALUES (?, ?)",
                (trade_date, "running")
            )
            conn.commit()
    
    def _log_fetch_complete(self, trade_date: str, status: str, total: int, 
                           duration: float, error: Optional[str] = None) -> None:
        """Log fetch completion."""
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                UPDATE snapshot_log 
                SET status = ?, total_stocks = ?, fetched_at = ?, 
                    duration_seconds = ?, error_message = ?
                WHERE trade_date = ? AND status = 'running'
                """,
                (status, total, datetime.now(timezone.utc).isoformat(), 
                 duration, error, trade_date)
            )
            conn.commit()
    
    def get_snapshot(self, trade_date: Optional[str] = None, 
                    min_change_pct: Optional[float] = None,
                    max_change_pct: Optional[float] = None,
                    sort_by: str = "change_pct",
                    limit: int = 100) -> List[Dict[str, Any]]:
        """Get cached snapshot data.
        
        Args:
            trade_date: Trade date. If None, uses latest available.
            min_change_pct: Minimum change percentage filter
            max_change_pct: Maximum change percentage filter
            sort_by: Column to sort by (change_pct, amount, volume)
            limit: Maximum number of results
            
        Returns:
            List of stock data dictionaries
        """
        try:
            conn = self._get_connection()
            
            # Get latest date if not specified
            if trade_date is None:
                row = conn.execute(
                    "SELECT MAX(trade_date) as date FROM daily_snapshots"
                ).fetchone()
                trade_date = row['date'] if row and row['date'] else None
            
            if not trade_date:
                return []
            
            # Build query
            query = "SELECT * FROM daily_snapshots WHERE trade_date = ?"
            params = [trade_date]
            
            if min_change_pct is not None:
                query += " AND change_pct >= ?"
                params.append(min_change_pct)
            
            if max_change_pct is not None:
                query += " AND change_pct <= ?"
                params.append(max_change_pct)
            
            # Sort
            valid_sort_columns = ['change_pct', 'amount', 'volume', 'turnover_rate', 'pe_ratio']
            if sort_by in valid_sort_columns:
                query += f" ORDER BY {sort_by} DESC"
            else:
                query += " ORDER BY change_pct DESC"
            
            query += " LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            logger.error(f"[MarketSnapshot] Failed to get snapshot: {e}")
            return []
    
    def get_snapshot_by_symbol(self, symbol: str, 
                               trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get snapshot data for a specific symbol."""
        try:
            conn = self._get_connection()
            
            if trade_date is None:
                row = conn.execute(
                    """
                    SELECT * FROM daily_snapshots 
                    WHERE symbol = ? 
                    ORDER BY trade_date DESC LIMIT 1
                    """,
                    (symbol,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM daily_snapshots WHERE symbol = ? AND trade_date = ?",
                    (symbol, trade_date)
                ).fetchone()
            
            return dict(row) if row else None
            
        except Exception as e:
            logger.error(f"[MarketSnapshot] Failed to get symbol snapshot: {e}")
            return None
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get snapshot statistics."""
        try:
            conn = self._get_connection()
            
            # Latest snapshot info
            latest = conn.execute(
                """
                SELECT trade_date, COUNT(*) as count, 
                       AVG(change_pct) as avg_change,
                       MAX(change_pct) as max_change,
                       MIN(change_pct) as min_change
                FROM daily_snapshots
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 1
                """
            ).fetchone()
            
            # Total snapshots
            total_snapshots = conn.execute(
                "SELECT COUNT(DISTINCT trade_date) FROM daily_snapshots"
            ).fetchone()[0]
            
            # Fetch log
            recent_logs = conn.execute(
                """
                SELECT trade_date, status, total_stocks, duration_seconds, fetched_at
                FROM snapshot_log
                ORDER BY created_at DESC
                LIMIT 5
                """
            ).fetchall()
            
            return {
                "latest_snapshot": dict(latest) if latest else None,
                "total_snapshots": total_snapshots,
                "db_path": str(self._db_path),
                "recent_logs": [dict(row) for row in recent_logs],
            }
            
        except Exception as e:
            logger.error(f"[MarketSnapshot] Failed to get statistics: {e}")
            return {"error": str(e)}
    
    def should_fetch_now(self) -> bool:
        """Check if it's time to fetch daily snapshot."""
        from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day
        
        now = datetime.now()
        today = cn_today_str()
        
        # Check if trading day
        if not is_cn_trading_day(today):
            return False
        
        # Parse fetch time
        try:
            hour, minute = map(int, self.fetch_time.split(":"))
        except ValueError:
            hour, minute = 15, 35
        
        # Check if current time >= fetch time
        if now.hour < hour or (now.hour == hour and now.minute < minute):
            return False
        
        # Check if already fetched today
        if self._is_snapshot_exists(today):
            return False
        
        return True


# Global instance
_snapshot_service: Optional[MarketSnapshotService] = None


def get_snapshot_service() -> MarketSnapshotService:
    """Get the global snapshot service instance."""
    global _snapshot_service
    if _snapshot_service is None:
        _snapshot_service = MarketSnapshotService()
    return _snapshot_service
