#!/usr/bin/env python3
"""Test script for market snapshot service."""

import asyncio
import os
import sys

os.environ["TA_DATA_CACHE_DIR"] = "./test_data_cache"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_fetch_snapshot():
    """Test fetching market snapshot."""
    print("=" * 60)
    print("Testing Market Snapshot Fetch")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    
    # Test fetch
    print("\nFetching market snapshot...")
    result = await service.fetch_and_cache_daily_snapshot("2024-11-29")
    
    print(f"Success: {result.get('success')}")
    print(f"Trade date: {result.get('trade_date')}")
    print(f"Total stocks: {result.get('total_stocks', 0)}")
    print(f"Duration: {result.get('duration_seconds', 0):.2f}s")
    
    if result.get('error'):
        print(f"Error: {result['error']}")
    
    return result.get('success')


def test_query_snapshot():
    """Test querying snapshot data."""
    print("\n" + "=" * 60)
    print("Testing Snapshot Query")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    
    # Query top gainers
    print("\nTop 10 gainers:")
    gainers = service.get_snapshot(
        trade_date="2024-11-29",
        min_change_pct=5.0,
        sort_by="change_pct",
        limit=10
    )
    
    for stock in gainers[:5]:
        print(f"  {stock['symbol']} {stock['name']}: +{stock['change_pct']:.2f}%")
    
    # Query top losers
    print("\nTop 10 losers:")
    losers = service.get_snapshot(
        trade_date="2024-11-29",
        max_change_pct=-5.0,
        sort_by="change_pct",
        limit=10
    )
    
    for stock in losers[:5]:
        print(f"  {stock['symbol']} {stock['name']}: {stock['change_pct']:.2f}%")
    
    # Query by symbol
    print("\nQuery single symbol (000001):")
    symbol_data = service.get_snapshot_by_symbol("000001", "2024-11-29")
    if symbol_data:
        print(f"  Name: {symbol_data['name']}")
        print(f"  Price: {symbol_data['price']}")
        print(f"  Change: {symbol_data['change_pct']:.2f}%")
    
    return True


def test_statistics():
    """Test getting statistics."""
    print("\n" + "=" * 60)
    print("Testing Statistics")
    print("=" * 60)
    
    from api.services.market_snapshot_service import get_snapshot_service
    
    service = get_snapshot_service()
    stats = service.get_statistics()
    
    print(f"\nDB Path: {stats.get('db_path')}")
    print(f"Total Snapshots: {stats.get('total_snapshots')}")
    
    latest = stats.get('latest_snapshot')
    if latest:
        print(f"\nLatest Snapshot:")
        print(f"  Date: {latest.get('trade_date')}")
        print(f"  Stocks: {latest.get('count')}")
        print(f"  Avg Change: {latest.get('avg_change', 0):.2f}%")
        print(f"  Max Change: {latest.get('max_change', 0):.2f}%")
        print(f"  Min Change: {latest.get('min_change', 0):.2f}%")
    
    logs = stats.get('recent_logs', [])
    if logs:
        print(f"\nRecent Logs:")
        for log in logs[:3]:
            print(f"  {log['trade_date']}: {log['status']} ({log.get('total_stocks', 0)} stocks)")
    
    return True


async def main():
    """Main test function."""
    print("\n" + "=" * 60)
    print("Market Snapshot Service Test")
    print("=" * 60 + "\n")
    
    # Test 1: Fetch snapshot
    fetch_success = await test_fetch_snapshot()
    
    if not fetch_success:
        print("\nFetch failed, skipping query tests")
        return
    
    # Test 2: Query snapshot
    test_query_snapshot()
    
    # Test 3: Statistics
    test_statistics()
    
    print("\n" + "=" * 60)
    print("Test Completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
