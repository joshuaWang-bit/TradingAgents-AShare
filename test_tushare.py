#!/usr/bin/env python3
"""Test script for Tushare data source."""

import os
import sys

os.environ["TA_DATA_CACHE_DIR"] = "./test_data_cache"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_tushare_initialization():
    """Test Tushare initialization."""
    print("=" * 60)
    print("Test 1: Tushare Initialization")
    print("=" * 60)
    
    from tradingagents.dataflows.plugins.builtin.tushare_source import TushareDataSource
    
    source = TushareDataSource()
    success = source.initialize()
    
    if success:
        print(f"[OK] Tushare initialized successfully")
        print(f"   Name: {source.name}")
        print(f"   Display: {source.display_name}")
        return True
    else:
        print(f"[FAIL] Failed to initialize Tushare")
        print(f"   Error: {source.get_last_error()}")
        return False


def test_tushare_market_snapshot():
    """Test Tushare market snapshot."""
    print("\n" + "=" * 60)
    print("Test 2: Tushare Market Snapshot")
    print("=" * 60)
    
    from tradingagents.dataflows.plugins.builtin.tushare_source import TushareDataSource
    
    source = TushareDataSource()
    if not source.initialize():
        print("[SKIP] Tushare not initialized")
        return False
    
    try:
        print("\nFetching daily basic data...")
        records = source.get_daily_basic("2024-11-29")
        
        print(f"[OK] Fetched {len(records)} stocks")
        
        # Show top 5
        print("\nTop 5 by change_pct:")
        sorted_records = sorted(records, key=lambda x: x.get('change_pct', 0), reverse=True)
        for r in sorted_records[:5]:
            print(f"   {r['symbol']} {r['name']}: {r['price']} ({r['change_pct']}%)")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tushare_stock_data():
    """Test Tushare individual stock data."""
    print("\n" + "=" * 60)
    print("Test 3: Tushare Stock Data")
    print("=" * 60)
    
    from tradingagents.dataflows.plugins.builtin.tushare_source import TushareDataSource
    
    source = TushareDataSource()
    if not source.initialize():
        print("[SKIP] Tushare not initialized")
        return False
    
    try:
        print("\nFetching 000001 data...")
        data = source.get_stock_data("000001", "2024-11-01", "2024-11-29")
        
        lines = data.strip().split('\n')
        print(f"[OK] Fetched data: {len(lines)} lines")
        
        # Print first few data lines
        data_lines = [l for l in lines if not l.startswith('#') and l.strip()]
        print(f"\nFirst 3 records:")
        for line in data_lines[1:4]:
            print(f"   {line}")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_market_snapshot_service_with_tushare():
    """Test MarketSnapshotService with Tushare."""
    print("\n" + "=" * 60)
    print("Test 4: MarketSnapshotService with Tushare")
    print("=" * 60)
    
    import asyncio
    from api.services.market_snapshot_service import get_snapshot_service
    
    async def run_test():
        service = get_snapshot_service()
        
        print("\nFetching market snapshot (will try Tushare first)...")
        result = await service.fetch_and_cache_daily_snapshot("2024-11-29")
        
        print(f"Success: {result.get('success')}")
        print(f"Source: {result.get('source', 'unknown')}")
        print(f"Total stocks: {result.get('total_stocks', 0)}")
        print(f"Duration: {result.get('duration_seconds', 0):.2f}s")
        
        if result.get('error'):
            print(f"Error: {result['error']}")
        
        return result.get('success')
    
    return asyncio.run(run_test())


def main():
    """Main test function."""
    print("\n" + "=" * 60)
    print("Tushare Data Source Test")
    print("=" * 60)
    
    # Check token
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        print("\n[FAIL] TUSHARE_TOKEN not set!")
        return
    
    print(f"\nTUSHARE_TOKEN: {token[:10]}...{token[-5:]}")
    
    try:
        # Run tests
        test1 = test_tushare_initialization()
        
        if not test1:
            print("\n[FAIL] Initialization failed, stopping tests")
            return
        
        test2 = test_tushare_market_snapshot()
        test3 = test_tushare_stock_data()
        test4 = test_market_snapshot_service_with_tushare()
        
        # Summary
        print("\n" + "=" * 60)
        print("Test Summary")
        print("=" * 60)
        print(f"Test 1 (Initialization): {'PASS' if test1 else 'FAIL'}")
        print(f"Test 2 (Market Snapshot): {'PASS' if test2 else 'FAIL'}")
        print(f"Test 3 (Stock Data): {'PASS' if test3 else 'FAIL'}")
        print(f"Test 4 (Snapshot Service): {'PASS' if test4 else 'FAIL'}")
        
        if all([test1, test2, test3, test4]):
            print("\n[OK] All tests passed!")
        else:
            print("\n[FAIL] Some tests failed")
            
    except Exception as e:
        print(f"\n[FAIL] Test error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
