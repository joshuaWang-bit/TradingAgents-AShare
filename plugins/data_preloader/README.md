# Data Preloader Plugin

数据源插件系统，支持多种数据源：AkShare 实时、预加载本地缓存、XBX 本地数据等。

## 功能特性

- **多数据源支持**：AkShare（实时）、Preloaded（预加载）、XBX（本地数据）
- **插件架构**：易于扩展自定义数据源
- **自动回退**：本地缓存 miss 时自动回退到实时获取
- **数据预加载**：每日定时从 AkShare 加载数据到本地 SQLite
- **XBX 兼容**：支持 XBX 本地数据库格式

## 快速开始

### 1. 使用默认数据源（AkShare 实时）

无需配置，系统默认使用 AkShare 实时获取数据。

### 2. 切换到预加载数据源

```bash
# 设置环境变量
export TA_DATA_SOURCE=preloaded
export TA_DATA_CACHE_DIR=./data_cache
export TA_DATA_MAX_AGE_HOURS=24

# 启动应用
python -m api.main
```

### 3. 手动触发数据预加载

```python
from tradingagents.dataflows.interface import preload_data

symbols = ["000001", "000002", "600000"]
result = preload_data(symbols, "2024-01-15")
print(result)
```

### 4. 使用 XBX 本地数据

```bash
# 设置 XBX 数据路径
export TA_DATA_SOURCE=xbx
export TA_XBX_DATA_PATH=E:\STOCKDATA
```

## 配置选项

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `TA_DATA_SOURCE` | 默认数据源 | `akshare` |
| `TA_DATA_CACHE_DIR` | 缓存目录 | `./data_cache` |
| `TA_DATA_MAX_AGE_HOURS` | 数据过期时间（小时） | `24` |
| `TA_DATA_FALLBACK_TO_REALTIME` | 缓存 miss 时回退到实时 | `true` |
| `TA_XBX_DATA_PATH` | XBX 数据路径 | `E:\STOCKDATA` |

### API 使用

```python
# 检查数据可用性
from tradingagents.dataflows.interface import check_data_availability

status = check_data_availability("000001", "2024-01-15")
print(status)
# {
#     "symbol": "000001",
#     "freshness": "fresh",
#     "has_price_data": True,
#     "record_count": 252,
#     "ready_for_analysis": True
# }

# 获取数据源信息
from tradingagents.dataflows.interface import get_data_source_info

info = get_data_source_info()
print(info)
```

## 自定义数据源

创建自定义数据源插件：

```python
# my_source.py
from tradingagents.dataflows.plugins import DataSource, DataSourceConfig
from tradingagents.dataflows.plugins.registry import DataSourceRegistry

class MyDataSource(DataSource):
    @property
    def name(self) -> str:
        return "my_source"
    
    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        # 实现数据获取逻辑
        pass
    
    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        # 实现基本面数据获取
        pass

# 注册插件
DataSourceRegistry.register("my_source", MyDataSource)
```

加载自定义插件：

```bash
export TA_DATA_SOURCE_PLUGINS=/path/to/plugins
python -m api.main
```

## 架构设计

```
tradingagents/dataflows/plugins/
├── base.py          # 抽象基类 DataSource
├── registry.py      # 插件注册表 DataSourceRegistry
├── loader.py        # 插件加载器
└── builtin/         # 内置插件
    ├── akshare_source.py    # AkShare 实时数据源
    ├── preloaded_source.py  # 预加载数据源
    └── xbx_source.py        # XBX 本地数据源
```

## 定时预加载

在 `api/main.py` 中添加定时任务：

```python
# 在 scheduler_loop 中添加
async def _data_preload_loop():
    while True:
        await asyncio.sleep(60)
        # 检查是否到达预加载时间（如 20:00）
        # 执行数据预加载
```

## 注意事项

1. **首次使用预加载数据源**：需要先手动运行一次预加载，或者等待定时任务执行
2. **XBX 数据源**：需要用户根据实际数据格式实现具体的数据读取逻辑
3. **存储空间**：预加载全市场数据约需 500MB-1GB 存储空间
4. **内存使用**：预加载数据源使用 SQLite，内存占用较低
