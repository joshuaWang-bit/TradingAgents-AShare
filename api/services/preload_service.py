"""Data preloading service for scheduled daily data loading.

This service manages the daily preloading of stock data from AkShare
to local SQLite cache. It can be run as a scheduled task or triggered manually.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from api.database import get_db_ctx

logger = logging.getLogger(__name__)


class DataPreloadService:
    """Service for managing data preloading operations."""
    
    def __init__(self):
        self._is_running = False
        self._current_operation: Optional[Dict] = None
        self._preload_time = os.getenv("TA_DATA_PRELOAD_TIME", "20:00")
        self._max_concurrent = int(os.getenv("TA_DATA_PRELOAD_CONCURRENT", "5"))
    
    def is_running(self) -> bool:
        """Check if a preload operation is currently running."""
        return self._is_running
    
    def get_status(self) -> Dict:
        """Get current preload status."""
        from tradingagents.dataflows.interface import get_preload_status
        
        status = get_preload_status()
        status["is_running"] = self._is_running
        status["current_operation"] = self._current_operation
        return status
    
    async def run_preload(
        self,
        symbols: Optional[List[str]] = None,
        trade_date: Optional[str] = None,
        progress_callback = None
    ) -> Dict:
        """Run data preloading for specified symbols.
        
        Args:
            symbols: List of symbols to preload. If None, loads all watchlist symbols.
            trade_date: Trade date to preload for. If None, uses current trading day.
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dict with operation results
        """
        if self._is_running:
            return {
                "success": False,
                "error": "Another preload operation is already running",
            }
        
        self._is_running = True
        
        try:
            # Determine trade date
            if trade_date is None:
                from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day, previous_cn_trading_day
                today = cn_today_str()
                trade_date = today if is_cn_trading_day(today) else previous_cn_trading_day(today)
            
            # Get symbols to preload
            if symbols is None:
                symbols = await self._get_symbols_to_preload()
            
            if not symbols:
                return {
                    "success": False,
                    "error": "No symbols to preload",
                }
            
            logger.info(f"[PreloadService] Starting preload for {len(symbols)} symbols on {trade_date}")
            
            # Initialize current operation tracking
            self._current_operation = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "trade_date": trade_date,
                "total_symbols": len(symbols),
                "processed": 0,
                "success": 0,
                "failed": 0,
            }
            
            # Get data source
            from tradingagents.dataflows.plugins.registry import get_data_source
            data_source = get_data_source("preloaded")
            
            if data_source is None:
                return {
                    "success": False,
                    "error": "Preloaded data source not available",
                }
            
            # Run preload
            def progress_wrapper(symbol: str, success: bool):
                self._current_operation["processed"] += 1
                if success:
                    self._current_operation["success"] += 1
                else:
                    self._current_operation["failed"] += 1
                
                if progress_callback:
                    asyncio.create_task(progress_callback(symbol, success))
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: data_source.preload_data(symbols, trade_date, progress_wrapper)
            )
            
            self._current_operation["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            logger.info(f"[PreloadService] Preload completed: {result}")
            return result
            
        except Exception as e:
            logger.error(f"[PreloadService] Preload failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            self._is_running = False
            self._current_operation = None
    
    async def _get_symbols_to_preload(self) -> List[str]:
        """Get list of symbols to preload.
        
        Combines symbols from:
        1. All users' watchlists
        2. All scheduled analysis tasks
        3. Recently analyzed symbols
        """
        symbols: Set[str] = set()
        
        try:
            with get_db_ctx() as db:
                # Get watchlist symbols
                from api.database import WatchlistItemDB
                watchlist_symbols = db.query(WatchlistItemDB.symbol).distinct().all()
                symbols.update(s[0] for s in watchlist_symbols)
                
                # Get scheduled analysis symbols
                from api.database import ScheduledAnalysisDB
                scheduled_symbols = db.query(ScheduledAnalysisDB.symbol).distinct().all()
                symbols.update(s[0] for s in scheduled_symbols)
                
                # Get recently analyzed symbols (last 7 days)
                from api.database import ReportDB
                from datetime import timedelta
                
                recent_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                recent_symbols = db.query(ReportDB.symbol).filter(
                    ReportDB.created_at >= recent_date
                ).distinct().all()
                symbols.update(s[0] for s in recent_symbols)
                
        except Exception as e:
            logger.error(f"[PreloadService] Failed to get symbols: {e}")
        
        return sorted(list(symbols))
    
    def should_preload_now(self) -> bool:
        """Check if it's time to run scheduled preload.
        
        Returns True if:
        - Current time >= preload_time
        - Not already run today
        - Is a trading day
        """
        from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day
        
        today = cn_today_str()
        
        # Check if trading day
        if not is_cn_trading_day(today):
            return False
        
        # Parse preload time
        try:
            hour, minute = map(int, self._preload_time.split(":"))
        except ValueError:
            hour, minute = 20, 0  # Default 20:00
        
        # Check if current time >= preload time
        now = datetime.now()
        if now.hour < hour or (now.hour == hour and now.minute < minute):
            return False
        
        # Check if already run today
        from tradingagents.dataflows.interface import get_preload_status
        status = get_preload_status()
        
        latest = status.get("latest_preload")
        if latest and latest.get("trade_date") == today:
            return False
        
        return True


# Global instance
_preload_service: Optional[DataPreloadService] = None


def get_preload_service() -> DataPreloadService:
    """Get the global preload service instance."""
    global _preload_service
    if _preload_service is None:
        _preload_service = DataPreloadService()
    return _preload_service
