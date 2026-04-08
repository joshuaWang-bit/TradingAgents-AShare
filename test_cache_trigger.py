#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test script to trigger and monitor data cache updates."""

import os
import sys
import time

# Set environment variables for testing
os.environ["TA_DATA_SOURCE"] = "smart_cache"
os.environ["TA_CACHE_MAX_SYMBOLS"] = "100"
os.environ["TA_CACHE_MAX_SIZE_MB"] = "100"
os.environ["TA_DATA_CACHE_DIR"] = "./test_data_cache"

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_initialization():
    """Test plugin initialization."""
    print("=" * 60)
    print("Step 1: Initializing Smart Cache Data Source")
    print("=" * 60)
    
    from tradingagents.dataflows.plugins.loader import initialize_plugins
    from tradingagents.dataflows.plugins.registry import get_data_source
    
    initialize_plugins()
    data_source = get_data_source("smart_cache")
    
    if data_source is None:
        print("[FAIL] Failed to initialize SmartCache data source")
        return False
    
    print(f"[OK] Data source initialized: {data_source.name}")
    print(f"   Display: {data_source.display_name}")
    print(f"   Description: {data_source.description}")
    print()
    return data_source


def test_price_data_fetch(data_source, symbols):
    """Test fetching price data."""
    print("=" * 60)
    print("Step 2: Fetching Price Data (with caching)")
    print("=" * 60)
    
    from tradingagents.dataflows.trade_calendar import previous_cn_trading_day
    
    end_date = "2024-11-29"  # Use a fixed date for testing
    start_date = "2024-01-01"
    
    results = []
    for symbol in symbols:
        print(f"\n[PRICE] Fetching {symbol}...")
        start_time = time.time()
        
        try:
            data = data_source.get_stock_data(symbol, start_date, end_date)
            duration = time.time() - start_time
            
            # Parse result
            lines = data.strip().split('\n')
            data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
            record_count = len(data_lines) - 1 if len(data_lines) > 1 else 0
            
            print(f"   [OK] Success: {record_count} records in {duration:.2f}s")
            results.append({"symbol": symbol, "records": record_count, "time": duration})
            
        except Exception as e:
            print(f"   [FAIL] Failed: {e}")
            results.append({"symbol": symbol, "error": str(e)})
    
    print()
    return results


def test_fundamentals_fetch(data_source, symbols):
    """Test fetching fundamentals data."""
    print("=" * 60)
    print("Step 3: Fetching Fundamentals Data")
    print("=" * 60)
    
    trade_date = "2024-11-29"
    results = []
    
    for symbol in symbols[:3]:
        print(f"\n[FUND] Fetching fundamentals for {symbol}...")
        start_time = time.time()
        
        try:
            data = data_source.get_fundamentals(symbol, trade_date)
            duration = time.time() - start_time
            
            size = len(data.encode('utf-8'))
            print(f"   [OK] Success: {size} bytes in {duration:.2f}s")
            results.append({"symbol": symbol, "size": size, "time": duration})
            
        except Exception as e:
            print(f"   [FAIL] Failed: {e}")
            results.append({"symbol": symbol, "error": str(e)})
    
    print()
    return results


def test_sentiment_fetch(data_source, symbols):
    """Test fetching market sentiment data."""
    print("=" * 60)
    print("Step 4: Fetching Market Sentiment Data")
    print("=" * 60)
    
    trade_date = "2024-11-29"
    results = []
    
    # Test LHB data
    for symbol in symbols[:2]:
        print(f"\n[LHB] Fetching LHB for {symbol}...")
        start_time = time.time()
        
        try:
            data = data_source.get_lhb_detail(symbol, trade_date)
            duration = time.time() - start_time
            
            size = len(data.encode('utf-8'))
            print(f"   [OK] Success: {size} bytes in {duration:.2f}s")
            results.append({"type": "lhb", "symbol": symbol, "size": size, "time": duration})
            
        except Exception as e:
            print(f"   [WARN] {e}")
    
    # Test ZT pool
    print(f"\n[ZT] Fetching ZT Pool for {trade_date}...")
    start_time = time.time()
    
    try:
        data = data_source.get_zt_pool(trade_date)
        duration = time.time() - start_time
        
        size = len(data.encode('utf-8'))
        print(f"   [OK] Success: {size} bytes in {duration:.2f}s")
        results.append({"type": "zt_pool", "size": size, "time": duration})
        
    except Exception as e:
        print(f"   [WARN] {e}")
    
    print()
    return results


def test_cache_statistics(data_source):
    """Test cache statistics."""
    print("=" * 60)
    print("Step 5: Cache Statistics")
    print("=" * 60)
    
    try:
        stats = data_source.get_cache_statistics()
        
        print(f"\n[STORAGE] Storage:")
        print(f"   Size: {stats.get('storage_mb', 0)} MB")
        print(f"   Max: {stats.get('max_mb', 0)} MB")
        print(f"   Utilization: {stats.get('utilization_percent', 0)}%")
        
        price_stats = stats.get('price_data', {})
        print(f"\n[PRICE] Price Data:")
        print(f"   Symbols: {price_stats.get('symbols', 0)}")
        print(f"   Records: {price_stats.get('records', 0)}")
        
        text_cache = stats.get('text_cache', {})
        print(f"\n[TEXT] Text Cache:")
        for cache_type, info in text_cache.items():
            print(f"   {cache_type}: {info.get('count', 0)} entries ({info.get('size_kb', 0)} KB)")
        
        hot_cache_stats = stats.get('hot_cache_stats', {})
        print(f"\n[HOT] Hot Cache (Memory):")
        for cache_name, cache_info in hot_cache_stats.items():
            hit_rate = cache_info.get('hit_rate', 0) * 100
            print(f"   {cache_name}: {cache_info.get('size', 0)}/{cache_info.get('max_size', 0)} "
                  f"(hit rate: {hit_rate:.1f}%)")
        
        ttl_config = stats.get('ttl_config', {})
        print(f"\n[TTL] TTL Configuration:")
        for key, value in ttl_config.items():
            print(f"   {key}: {value}")
        
    except Exception as e:
        print(f"   [FAIL] Failed to get statistics: {e}")
    
    print()


def test_cache_hit(data_source, symbols):
    """Test cache hit performance."""
    print("=" * 60)
    print("Step 6: Testing Cache Hit Performance")
    print("=" * 60)
    
    end_date = "2024-11-29"
    start_date = "2024-11-01"
    
    for symbol in symbols[:2]:
        print(f"\n[CACHE] Testing cache hit for {symbol}:")
        
        # First fetch (should be from source)
        t1 = time.time()
        data1 = data_source.get_stock_data(symbol, start_date, end_date)
        duration1 = time.time() - t1
        print(f"   1st fetch: {duration1:.3f}s (from source)")
        
        # Second fetch (should be from cache)
        t2 = time.time()
        data2 = data_source.get_stock_data(symbol, start_date, end_date)
        duration2 = time.time() - t2
        print(f"   2nd fetch: {duration2:.3f}s (from cache)")
        
        speedup = duration1 / duration2 if duration2 > 0 else 0
        print(f"   >> Speedup: {speedup:.1f}x")
    
    print()


def main():
    """Main test function."""
    print("\n" + "=" * 60)
    print("Smart Cache Data Source Test")
    print("=" * 60 + "\n")
    
    # Test symbols (popular A-share stocks)
    test_symbols = ["000001", "000002", "600519"]
    
    # Step 1: Initialize
    data_source = test_initialization()
    if not data_source:
        sys.exit(1)
    
    # Step 2: Fetch price data
    price_results = test_price_data_fetch(data_source, test_symbols)
    
    # Step 3: Fetch fundamentals
    fundamentals_results = test_fundamentals_fetch(data_source, test_symbols)
    
    # Step 4: Fetch sentiment
    sentiment_results = test_sentiment_fetch(data_source, test_symbols)
    
    # Step 5: Statistics
    test_cache_statistics(data_source)
    
    # Step 6: Cache hit test
    test_cache_hit(data_source, test_symbols)
    
    # Final statistics
    print("=" * 60)
    print("Final Summary")
    print("=" * 60)
    
    success_count = sum(1 for r in price_results if "error" not in r)
    print(f"\n[OK] Price data: {success_count}/{len(price_results)} symbols cached successfully")
    
    if fundamentals_results:
        fund_success = sum(1 for r in fundamentals_results if "error" not in r)
        print(f"[OK] Fundamentals: {fund_success}/{len(fundamentals_results)} symbols cached")
    
    print("\n[DONE] All tests completed!")
    print(f"\nCache directory: {os.environ['TA_DATA_CACHE_DIR']}")


if __name__ == "__main__":
    main()
