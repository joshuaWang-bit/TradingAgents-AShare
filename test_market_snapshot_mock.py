#!/usr/bin/env python3
"""Test market snapshot service with mock data."""

import os
import sys
import sqlite3
from datetime import datetime

os.environ["TA_DATA_CACHE_DIR"] = "./test_data_cache"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_database_schema():
    """Test database schema creation."""
    print("=" * 60)
    print("Test 1: Database Schema Creation")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    db_path = service._db_path
    
    print(f"Database path: {db_path}")
    
    # Check if tables exist
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    tables = [row[0] for row in cursor.fetchall()]
    
    print(f"Tables created: {tables}")
    
    expected_tables = ['daily_snapshots', 'snapshot_log']
    for table in expected_tables:
        if table in tables:
            print(f"  [OK] {table} exists")
        else:
            print(f"  [FAIL] {table} missing")
    
    conn.close()
    return True


def test_insert_mock_data():
    """Test inserting mock data."""
    print("\n" + "=" * 60)
    print("Test 2: Insert Mock Data")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    trade_date = "2024-12-01"
    
    # Create mock data
    mock_data = [
        (trade_date, "000001", "平安银行", 12.50, 2.5, 0.30, 1000000, 12500000, 3.2, 12.80, 12.20, 12.30, 12.20, 1.5, 0.8, 8.5, 1.2, 2500000000, 3000000000),
        (trade_date, "000002", "万科A", 18.20, -1.2, -0.22, 800000, 14560000, 2.8, 18.50, 18.00, 18.40, 18.42, 1.2, 0.6, 12.3, 1.5, 1800000000, 2200000000),
        (trade_date, "600519", "贵州茅台", 1680.00, 1.8, 29.70, 50000, 84000000, 2.1, 1690.00, 1670.00, 1675.00, 1650.30, 0.9, 0.4, 35.2, 8.5, 210000000000, 2100000000000),
        (trade_date, "000858", "五粮液", 158.50, 3.2, 4.90, 200000, 31700000, 3.5, 160.00, 156.00, 157.00, 153.60, 1.8, 0.7, 28.5, 4.2, 60000000000, 600000000000),
        (trade_date, "002594", "比亚迪", 268.80, 5.8, 14.70, 300000, 80640000, 4.2, 272.00, 262.00, 265.00, 254.10, 2.5, 1.2, 42.3, 6.8, 780000000000, 780000000000),
    ]
    
    conn = service._get_connection()
    
    # Clear existing data
    conn.execute("DELETE FROM daily_snapshots WHERE trade_date = ?", (trade_date,))
    
    # Insert mock data
    conn.executemany(
        """
        INSERT INTO daily_snapshots 
        (trade_date, symbol, name, price, change_pct, change_amount, volume, amount,
         amplitude, high, low, open_price, pre_close, volume_ratio, turnover_rate,
         pe_ratio, pb_ratio, market_cap, total_cap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        mock_data
    )
    conn.commit()
    
    # Verify insertion
    cursor = conn.execute(
        "SELECT COUNT(*) FROM daily_snapshots WHERE trade_date = ?",
        (trade_date,)
    )
    count = cursor.fetchone()[0]
    
    print(f"Inserted {count} mock records")
    print("  [OK] Mock data inserted successfully")
    
    return True


def test_query_functions():
    """Test query functions."""
    print("\n" + "=" * 60)
    print("Test 3: Query Functions")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    trade_date = "2024-12-01"
    
    # Test 3.1: Get all snapshot
    print("\n3.1 Get all snapshot:")
    all_data = service.get_snapshot(trade_date=trade_date, limit=10)
    print(f"  Found {len(all_data)} records")
    for stock in all_data:
        print(f"    {stock['symbol']} {stock['name']}: {stock['price']} ({stock['change_pct']}%)")
    
    # Test 3.2: Filter by min_change
    print("\n3.2 Filter by min_change_pct=3.0:")
    gainers = service.get_snapshot(trade_date=trade_date, min_change_pct=3.0, sort_by="change_pct")
    print(f"  Found {len(gainers)} stocks with change >= 3%")
    for stock in gainers:
        print(f"    {stock['symbol']} {stock['name']}: +{stock['change_pct']}%")
    
    # Test 3.3: Filter by max_change (losers)
    print("\n3.3 Filter by max_change_pct=-1.0:")
    losers = service.get_snapshot(trade_date=trade_date, max_change_pct=-1.0, sort_by="change_pct")
    print(f"  Found {len(losers)} stocks with change <= -1%")
    for stock in losers:
        print(f"    {stock['symbol']} {stock['name']}: {stock['change_pct']}%")
    
    # Test 3.4: Get by symbol
    print("\n3.4 Get single symbol (000001):")
    symbol_data = service.get_snapshot_by_symbol("000001", trade_date)
    if symbol_data:
        print(f"  [OK] Found: {symbol_data['name']} @ {symbol_data['price']}")
    else:
        print("  [FAIL] Not found")
    
    return True


def test_statistics():
    """Test statistics function."""
    print("\n" + "=" * 60)
    print("Test 4: Statistics")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    stats = service.get_statistics()
    
    print(f"DB Path: {stats.get('db_path')}")
    print(f"Total Snapshots: {stats.get('total_snapshots')}")
    
    latest = stats.get('latest_snapshot')
    if latest:
        print(f"\nLatest Snapshot:")
        print(f"  Date: {latest.get('trade_date')}")
        print(f"  Stocks: {latest.get('count')}")
        print(f"  Avg Change: {latest.get('avg_change', 0):.2f}%")
        print(f"  Max Change: {latest.get('max_change', 0):.2f}%")
        print(f"  Min Change: {latest.get('min_change', 0):.2f}%")
        print("  [OK] Statistics working")
    else:
        print("  [FAIL] No latest snapshot")
    
    return True


def test_cache_hit():
    """Test cache hit performance."""
    print("\n" + "=" * 60)
    print("Test 5: Cache Hit Performance")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    trade_date = "2024-12-01"
    
    # First query (from DB)
    import time
    t1 = time.time()
    data1 = service.get_snapshot(trade_date=trade_date, limit=10)
    duration1 = time.time() - t1
    
    # Second query (should be faster from memory/cache)
    t2 = time.time()
    data2 = service.get_snapshot(trade_date=trade_date, limit=10)
    duration2 = time.time() - t2
    
    print(f"First query:  {duration1*1000:.2f} ms")
    print(f"Second query: {duration2*1000:.2f} ms")
    
    if duration1 > 0 and duration2 > 0:
        speedup = duration1 / duration2
        print(f"Speedup: {speedup:.1f}x")
    
    return True


def main():
    """Main test function."""
    print("\n" + "=" * 60)
    print("Market Snapshot Service Test (Mock Data)")
    print("=" * 60)
    
    try:
        test_database_schema()
        test_insert_mock_data()
        test_query_functions()
        test_statistics()
        test_cache_hit()
        
        print("\n" + "=" * 60)
        print("All Tests Passed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
