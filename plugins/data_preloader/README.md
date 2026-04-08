# Data Preloader Plugin

数据源插件系统，支持多种数据源：AkShare 实时、按需智能缓存、全量预加载、XBX 本地数据等。

## 数据源对比

| 数据源 | 模式 | 存储策略 | 适用场景 | 存储空间 |
|--------|------|----------|----------|----------|
| `akshare` | 实时获取 | 无缓存 | 开发测试、数据量小 | 无 |
| `smart_cache` | **按需加载** | LRU淘汰、分层TTL | **生产环境推荐** | 可控（默认500MB） |
| `preloaded` | 定时全量 | 全市场缓存 | 高性能分析场景 | 较大（1-2GB） |
| `xbx` | 本地文件 | 依赖外部数据 | 已有XBX数据的用户 | 取决于XBX数据 |

## 推荐使用：Smart Cache（按需加载）

**Smart Cache** 是生产环境推荐的数据源，特点：
- **按需加载**: Agent 请求时才从 AkShare 获取数据
- **LRU 淘汰**: 缓存满时自动淘汰最少使用的数据
- **分层存储**: 热数据（内存）→ 温数据（SQLite）→ 冷数据（AkShare）
- **多类型缓存**: 价格、基本面、市场情绪分别设置 TTL
- **存储可控**: 可配置最大存储空间和缓存条目数

### 快速开始

```bash
# 使用 Smart Cache（推荐）
export TA_DATA_SOURCE=smart_cache
export TA_CACHE_MAX_SYMBOLS=1000        # 最多缓存1000只股票
export TA_CACHE_MAX_DAYS_PER_SYMBOL=90  # 每只股票保留90天数据
export TA_CACHE_MAX_SIZE_MB=500         # 最大占用500MB

python -m api.main
```

### 配置选项

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `TA_DATA_SOURCE` | 数据源名称 | `akshare` |
| `TA_CACHE_MAX_SYMBOLS` | 最大缓存股票数 | `1000` |
| `TA_CACHE_MAX_DAYS_PER_SYMBOL` | 每只股票最大天数 | `90` |
| `TA_CACHE_MAX_SIZE_MB` | 最大缓存大小(MB) | `500` |
| `TA_DATA_CACHE_DIR` | 缓存目录 | `./data_cache` |

### TTL 配置（缓存生存时间）

| 环境变量 | 说明 | 默认值 | 理由 |
|----------|------|--------|------|
| `TA_CACHE_TTL_PRICE_HOURS` | 价格数据 TTL | `24` | 日内可能有更新 |
| `TA_CACHE_TTL_FUNDAMENTALS_DAYS` | 基本面 TTL | `7` | 变化不频繁 |
| `TA_CACHE_TTL_FINANCIAL_DAYS` | 财务报表 TTL | `30` | 季报/年报周期长 |
| `TA_CACHE_TTL_SENTIMENT_HOURS` | 市场情绪 TTL | `12` | 日内数据，收市后不变 |
| `TA_CACHE_TTL_NEWS_HOURS` | 新闻 TTL | `6` | 时效性强 |
| `TA_CACHE_TTL_INSIDER_DAYS` | 内幕交易 TTL | `3` | 变动不频繁 |

## 数据缓存清单

### ✅ 已缓存的数据

| 数据类型 | 具体数据 | TTL | 缓存位置 |
|----------|----------|-----|----------|
| **价格数据** | `get_stock_data` | 24小时 | SQLite + 内存LRU |
| **基本面** | `get_fundamentals` | 7天 | SQLite + 内存LRU |
| **财务报表** | `get_balance_sheet`<br>`get_cashflow`<br>`get_income_statement` | 30天 | SQLite + 内存LRU |
| **市场情绪** | `get_lhb_detail`<br>`get_zt_pool`<br>`get_board_fund_flow`<br>`get_individual_fund_flow` | 12小时 | SQLite + 内存LRU |
| **新闻** | `get_news` | 6小时 | SQLite |
| **内幕交易** | `get_insider_transactions` | 3天 | SQLite |

### ❌ 实时获取的数据

| 数据 | 原因 |
|------|------|
| `get_realtime_quotes` | 实时行情必须实时 |
| `get_global_news` | 全球新闻时效性强 |
| `get_hot_stocks_xq` | 热搜实时变化 |
| `get_indicators` | 从缓存价格数据本地计算 |

## API 使用

```python
# 检查缓存统计
GET /v1/data/status

# 响应示例
{
  "is_running": false,
  "cache_path": "./data_cache/smart_cache.db",
  "price_data": {
    "symbols": 523,
    "records": 45021
  },
  "text_cache": {
    "fundamentals": {"count": 125, "size_kb": 5120},
    "sentiment": {"count": 48, "size_kb": 256},
    "news": {"count": 200, "size_kb": 1024}
  },
  "storage_mb": 128.5,
  "utilization_percent": 25.7,
  "ttl_config": {
    "price_hours": 24,
    "fundamentals_days": 7,
    "sentiment_hours": 12
  }
}
```

```python
# 查询数据可用性
GET /v1/data/availability/000001

# 响应示例
{
  "symbol": "000001",
  "trade_date": "2024-01-15",
  "freshness": "fresh",
  "has_price_data": true,
  "has_fundamentals": true,
  "record_count": 90,
  "ready_for_analysis": true
}
```

## 数据更新逻辑

### Smart Cache 更新流程

```
Agent 请求数据
    │
    ▼
┌─────────────────┐
│  1. 检查热缓存   │◄── 内存 LRU (最近100条)
│  (内存)         │    命中 → 直接返回
└────────┬────────┘
         │ 未命中
         ▼
┌─────────────────┐
│  2. 检查温缓存   │◄── SQLite 数据库
│  (SQLite)       │    命中 → 返回并提升到热缓存
└────────┬────────┘
         │ 未命中
         ▼
┌─────────────────┐
│  3. 从 AkShare  │◄── 网络请求
│  获取数据       │    获取后写入 SQLite
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. 检查缓存限制 │◄── 超出限制则 LRU 淘汰
│  (LRU 淘汰)     │    删除最少使用的数据
└─────────────────┘
```

### TTL 过期策略

不同类型数据有不同的生存时间：

```
价格数据 (24h)          基本面 (7d)            市场情绪 (12h)
    │                      │                       │
    ▼                      ▼                       ▼
┌─────────┐           ┌─────────┐            ┌─────────┐
│ 日内有效 │           │ 周级有效 │            │ 当日有效 │
│ 收盘后   │           │ 财报季   │            │ 次日重置 │
│ 可重用   │           │ 不变    │            │         │
└─────────┘           └─────────┘            └─────────┘
```

### 淘汰策略

当缓存达到限制时，按以下优先级淘汰数据：

1. **数据过期** (超过 TTL) - 每小时清理
2. **最久未访问** (last_accessed_at 最早)
3. **访问次数最少** (access_count 最少)

### 自动清理

- **每小时自动清理**: 删除超过 TTL 的数据
- **实时 LRU**: 写入新数据时，如果超出限制立即淘汰

## 使用场景建议

### 场景1: 个人用户，关注几十只股票
```bash
export TA_DATA_SOURCE=smart_cache
export TA_CACHE_MAX_SYMBOLS=200
export TA_CACHE_MAX_SIZE_MB=100
```

### 场景2: 专业分析，关注全市场
```bash
export TA_DATA_SOURCE=smart_cache
export TA_CACHE_MAX_SYMBOLS=5000
export TA_CACHE_MAX_SIZE_MB=2000
export TA_CACHE_MAX_DAYS_PER_SYMBOL=252  # 一年数据
```

### 场景3: 已有 XBX 本地数据
```bash
export TA_DATA_SOURCE=xbx
export TA_XBX_DATA_PATH=E:\STOCKDATA
```

### 场景4: 开发测试
```bash
export TA_DATA_SOURCE=akshare  # 不缓存，每次实时获取
```

## 自定义数据源插件

创建自定义数据源:

```python
# my_source.py
from tradingagents.dataflows.plugins import DataSource, DataSourceConfig
from tradingagents.dataflows.plugins.registry import DataSourceRegistry

class MyDataSource(DataSource):
    @property
    def name(self) -> str:
        return "my_source"
    
    @property
    def display_name(self) -> str:
        return "我的数据源"
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        # 实现数据获取逻辑
        pass
    
    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        # 实现基本面数据获取
        pass

# 注册插件
DataSourceRegistry.register("my_source", MyDataSource)
```

加载自定义插件:

```bash
export TA_DATA_SOURCE_PLUGINS=/path/to/plugins
python -m api.main
```

## 监控和调优

### 查看缓存命中率

```bash
curl http://localhost:8000/v1/data/status
```

### 指标说明

| 指标 | 说明 | 健康值 |
|------|------|--------|
| `utilization_percent` | 缓存空间使用率 | < 80% |
| `hit_rate` | 热缓存命中率 | > 50% |
| `fundamentals.count` | 缓存的基本面数量 | 根据需求 |
| `sentiment.count` | 缓存的市场情绪数据 | 当日股票数 |

### 调优建议

- **命中率低**: 增大 `TA_CACHE_MAX_SYMBOLS`
- **基本面缓存少**: 增大 `TA_CACHE_TTL_FUNDAMENTALS_DAYS`
- **存储满了**: 增大 `TA_CACHE_MAX_SIZE_MB` 或减小 TTL
- **新闻太旧**: 减小 `TA_CACHE_TTL_NEWS_HOURS`

## 注意事项

1. **首次访问较慢**: 冷启动时需要从 AkShare 获取数据
2. **SQLite 并发**: 单进程/线程访问，多进程部署需要每个进程独立缓存
3. **XBX 需要自行实现**: XbxDataSource 是模板，需要根据实际数据格式实现
4. **TTL 过期后自动刷新**: 过期数据会自动重新获取，无需手动干预
