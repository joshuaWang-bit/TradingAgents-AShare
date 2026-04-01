from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from tradingagents.dataflows.config import get_config


DEFAULT_XBX_DATA_DIR = r"E:\STOCKDATA"
_CACHE_DIR = Path(__file__).resolve().parent / "data_cache"


def get_xbx_data_dir() -> Path:
    config = get_config()
    configured = str(config.get("xbx_data_dir") or "").strip()
    if configured:
        return Path(configured)
    env_value = os.getenv("TA_XBX_DATA_DIR", "").strip()
    if env_value:
        return Path(env_value)
    return Path(DEFAULT_XBX_DATA_DIR)


def normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    match = re.search(r"(\d{6})(?:\.(SH|SZ|BJ|SS))?", raw)
    if not match:
        raise ValueError(f"Unsupported A-share symbol: {symbol}")
    code = match.group(1)
    suffix = (match.group(2) or "").upper()
    if suffix == "SS":
        suffix = "SH"
    if not suffix:
        if code.startswith(("8", "9")):
            suffix = "BJ"
        elif code.startswith(("5", "6")):
            suffix = "SH"
        else:
            suffix = "SZ"
    return f"{code}.{suffix}"


def extract_code(symbol: str) -> str:
    return normalize_symbol(symbol).split(".", 1)[0]


def market_prefix(symbol: str) -> str:
    market = normalize_symbol(symbol).split(".", 1)[1]
    return market.lower()


def stock_candidates(symbol: str) -> list[str]:
    normalized = normalize_symbol(symbol)
    code, market = normalized.split(".", 1)
    prefix = market.lower()
    return [
        f"{prefix}{code}.csv",
        f"{normalized}.csv",
        f"{code}.{market}.csv",
        f"{code}.csv",
    ]


def find_stock_file(symbol: str, base_dir: Optional[Path] = None) -> Optional[Path]:
    root = Path(base_dir or get_xbx_data_dir())
    folder = root / "stock-trading-data-pro"
    for candidate in stock_candidates(symbol):
        path = folder / candidate
        if path.exists():
            return path
    return None


def find_notice_file(symbol: str, base_dir: Optional[Path] = None) -> Optional[Path]:
    root = Path(base_dir or get_xbx_data_dir())
    folder = root / "stock-notices-title"
    normalized = normalize_symbol(symbol)
    code, market = normalized.split(".", 1)
    prefix = market.lower()
    for candidate in (f"{prefix}{code}.csv", f"{normalized}.csv", f"{code}.csv"):
        path = folder / candidate
        if path.exists():
            return path
    return None


def find_financial_file(symbol: str, base_dir: Optional[Path] = None) -> Optional[Path]:
    root = Path(base_dir or get_xbx_data_dir())
    folder = root / "stock-fin-data-xbx" / f"{market_prefix(symbol)}{extract_code(symbol)}"
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.csv"))
    return files[0] if files else None


def index_candidates(symbol: str) -> list[str]:
    raw = str(symbol or "").strip()
    code_match = re.search(r"(\d{6})", raw)
    if not code_match:
        return []
    code = code_match.group(1)
    upper = raw.upper()
    if upper.endswith(".SH"):
        return [f"{code}.SH.csv", f"sh{code}.csv"]
    if upper.endswith(".SZ"):
        return [f"{code}.SZ.csv", f"sz{code}.csv"]
    return [f"sh{code}.csv", f"sz{code}.csv", f"{code}.SH.csv", f"{code}.SZ.csv"]


def find_index_file(symbol: str, base_dir: Optional[Path] = None) -> Optional[Path]:
    root = Path(base_dir or get_xbx_data_dir())
    folder = root / "stock-main-index-data"
    for candidate in index_candidates(symbol):
        path = folder / candidate
        if path.exists():
            return path
    return None


def read_csv_flexible(
    path: Path,
    *,
    required_columns: Optional[Iterable[str]] = None,
    nrows: Optional[int] = None,
) -> pd.DataFrame:
    required = set(required_columns or [])
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030", "gbk", "utf-8"):
        for skiprows in (0, 1):
            try:
                df = pd.read_csv(
                    path,
                    encoding=encoding,
                    skiprows=skiprows,
                    nrows=nrows,
                    low_memory=False,
                )
                if required and not required.issubset(set(df.columns)):
                    continue
                return df
            except Exception as exc:  # pragma: no cover - best-effort fallback path
                last_error = exc
                continue
    if last_error is not None:
        raise last_error
    raise FileNotFoundError(str(path))


def load_trade_dates() -> tuple[list[pd.Timestamp], set[pd.Timestamp]]:
    root = get_xbx_data_dir()
    path = root / "trade_date.csv"
    if not path.exists():
        return [], set()
    df = read_csv_flexible(path, required_columns={"trade_date"})
    dates = sorted(
        ts.normalize()
        for ts in pd.to_datetime(df["trade_date"], errors="coerce")
        if pd.notna(ts)
    )
    return dates, set(dates)


def load_stock_daily_df(symbol: str, base_dir: Optional[Path] = None) -> pd.DataFrame:
    path = find_stock_file(symbol, base_dir=base_dir)
    if path is None:
        return pd.DataFrame()
    df = read_csv_flexible(path, required_columns={"交易日期", "开盘价", "最高价", "最低价", "收盘价"})
    df = df.copy()
    rename_map = {
        "股票代码": "symbol",
        "股票名称": "name",
        "交易日期": "trade_date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "前收盘价": "previous_close",
        "成交量": "volume",
        "成交额": "amount",
        "流通市值": "float_market_value",
        "总市值": "total_market_value",
        "换手率": "turnover_rate",
        "09:35收盘价": "close_0935",
        "09:45收盘价": "close_0945",
        "09:55收盘价": "close_0955",
    }
    df = df.rename(columns=rename_map)
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    for column in (
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "volume",
        "amount",
        "float_market_value",
        "total_market_value",
        "turnover_rate",
        "close_0935",
        "close_0945",
        "close_0955",
    ):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if "symbol" not in df.columns:
        df["symbol"] = normalize_symbol(symbol)
    else:
        df["symbol"] = df["symbol"].astype(str).map(lambda value: _normalize_local_symbol(value, symbol))
    if "name" in df.columns:
        df["name"] = df["name"].astype(str)
    return df


def load_notices_df(symbol: str, base_dir: Optional[Path] = None) -> pd.DataFrame:
    path = find_notice_file(symbol, base_dir=base_dir)
    if path is None:
        return pd.DataFrame()
    df = read_csv_flexible(path, required_columns={"公告日期", "公告标题"})
    rename_map = {
        "公告日期": "notice_date",
        "股票代码": "symbol",
        "股票名称": "name",
        "公告标题": "title",
    }
    df = df.rename(columns=rename_map)
    df["notice_date"] = pd.to_datetime(df["notice_date"], errors="coerce")
    df = df.dropna(subset=["notice_date"]).sort_values("notice_date", ascending=False).reset_index(drop=True)
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).map(lambda value: _normalize_local_symbol(value, symbol))
    return df


def load_financial_df(symbol: str, base_dir: Optional[Path] = None) -> pd.DataFrame:
    path = find_financial_file(symbol, base_dir=base_dir)
    if path is None:
        return pd.DataFrame()
    df = read_csv_flexible(path, required_columns={"stock_code", "report_date"})
    df = df.copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    if "publish_date" in df.columns:
        df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    df = df.dropna(subset=["report_date"]).sort_values("report_date", ascending=False).reset_index(drop=True)
    if "stock_code" in df.columns:
        df["stock_code"] = df["stock_code"].astype(str).map(lambda value: _normalize_local_symbol(value, symbol))
    return df


def load_index_daily_df(symbol: str, base_dir: Optional[Path] = None) -> pd.DataFrame:
    path = find_index_file(symbol, base_dir=base_dir)
    if path is None:
        return pd.DataFrame()
    df = read_csv_flexible(path)
    rename_map = {
        "candle_end_time": "trade_date",
        "date": "trade_date",
        "Date": "trade_date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "amount": "amount",
        "volume": "volume",
    }
    df = df.rename(columns=rename_map)
    required = {"trade_date", "open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "amount", "volume"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


@lru_cache(maxsize=8)
def build_stock_name_map(base_dir_str: str) -> dict[str, str]:
    base_dir = Path(base_dir_str)
    cache_path = _CACHE_DIR / "xbx_stock_name_map.json"
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {str(k): str(v) for k, v in payload.items()}
        except Exception:
            pass
    mapping: dict[str, str] = {}
    price_folder = base_dir / "stock-trading-data-pro"
    if price_folder.exists():
        for file_path in price_folder.glob("*.csv"):
            try:
                df = read_csv_flexible(
                    file_path,
                    required_columns={"股票代码", "股票名称"},
                    nrows=1,
                )
                if df.empty:
                    continue
                row = df.iloc[0]
                symbol = _normalize_local_symbol(str(row.get("股票代码", "")), file_path.stem)
                name = str(row.get("股票名称", "")).strip()
                if symbol and name:
                    mapping[name] = symbol
            except Exception:
                continue

    notices_folder = base_dir / "stock-notices-title"
    if notices_folder.exists():
        for file_path in notices_folder.glob("*.csv"):
            try:
                df = read_csv_flexible(
                    file_path,
                    required_columns={"股票代码", "股票名称"},
                    nrows=1,
                )
                if df.empty:
                    continue
                row = df.iloc[0]
                symbol = _normalize_local_symbol(str(row.get("股票代码", "")), file_path.stem)
                name = str(row.get("股票名称", "")).strip()
                if symbol and name:
                    mapping[name] = symbol
            except Exception:
                continue
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(mapping, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return mapping


def get_stock_name_map() -> dict[str, str]:
    return build_stock_name_map(str(get_xbx_data_dir()))


def _normalize_local_symbol(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    if raw:
        upper = raw.upper()
        match = re.search(r"(\d{6})(?:\.(SH|SZ|BJ|SS))?", upper)
        if match:
            code = match.group(1)
            suffix = (match.group(2) or "").upper()
            if suffix == "SS":
                suffix = "SH"
            if suffix:
                return f"{code}.{suffix}"
        lower = raw.lower()
        match = re.search(r"(sh|sz|bj)(\d{6})", lower)
        if match:
            return f"{match.group(2)}.{match.group(1).upper()}"
    try:
        return normalize_symbol(fallback)
    except Exception:
        return str(fallback or "").upper()
