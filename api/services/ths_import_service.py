from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from api.database import ImportedPortfolioPositionDB, ThsImportConfigDB
from api.services import scheduled_service
from tradingagents.agents.utils.context_utils import normalize_user_context


SOURCE_NAME = "ths_local"

HOLDINGS_ALIASES = {
    "symbol": ["证券代码", "股票代码", "代码", "证券", "股票"],
    "security_name": ["证券名称", "股票名称", "名称", "证券名称/代码"],
    "current_position": ["股票余额", "证券数量", "当前持仓", "持仓", "持股数", "余额", "持仓数量"],
    "available_position": ["可用余额", "可卖数量", "可用数量", "可卖", "可用股数"],
    "average_cost": ["成本价", "参考成本价", "摊薄成本价", "买入成本", "持仓成本", "成本"],
    "market_value": ["市值", "最新市值", "持仓市值", "股票市值"],
}

TRADES_ALIASES = {
    "trade_date": ["成交日期", "日期", "业务日期", "委托日期"],
    "trade_time": ["成交时间", "时间", "委托时间"],
    "symbol": ["证券代码", "股票代码", "代码", "证券"],
    "security_name": ["证券名称", "股票名称", "名称"],
    "action": ["操作", "买卖标志", "买卖", "委托类别", "方向", "业务名称"],
    "quantity": ["成交数量", "数量", "成交股数", "委托数量"],
    "price": ["成交均价", "成交价格", "价格", "成交价"],
    "amount": ["成交金额", "发生金额", "金额", "成交额"],
}


def sync_ths_portfolio(
    db: Session,
    user_id: str,
    holdings_filename: str,
    holdings_content: bytes,
    trades_filename: str | None = None,
    trades_content: bytes | None = None,
    auto_apply_scheduled: bool = True,
) -> dict[str, Any]:
    """Replace the user's imported THS snapshot with a freshly uploaded export."""

    if not holdings_content:
        raise ValueError("请先上传同花顺导出的持仓文件")

    holdings_rows = _parse_holdings_file(holdings_filename, holdings_content)
    trade_rows = _parse_trades_file(trades_filename or "trades.csv", trades_content) if trades_content else []

    if not holdings_rows and not trade_rows:
        raise ValueError("未从同花顺导出文件中解析到有效持仓或成交记录")

    now = datetime.now(timezone.utc)
    trades_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(
        trade_rows,
        key=lambda item: (item.get("trade_date") or "", item.get("trade_time") or ""),
        reverse=True,
    ):
        trades_by_symbol[row["symbol"]].append(row)

    holdings_by_symbol = {row["symbol"]: row for row in holdings_rows}
    total_market_value = sum(
        row.get("market_value") or 0.0
        for row in holdings_rows
        if (row.get("market_value") or 0.0) > 0
    )

    db.query(ImportedPortfolioPositionDB).filter(
        ImportedPortfolioPositionDB.user_id == user_id,
        ImportedPortfolioPositionDB.source == SOURCE_NAME,
    ).delete()

    all_symbols = sorted(set(holdings_by_symbol) | set(trades_by_symbol))
    for symbol in all_symbols:
        holding = holdings_by_symbol.get(symbol, {})
        trade_points = trades_by_symbol.get(symbol, [])
        market_value = holding.get("market_value")
        position_pct = None
        if total_market_value > 0 and market_value is not None:
            position_pct = round((market_value / total_market_value) * 100, 4)

        latest_trade = trade_points[0] if trade_points else None
        db.add(
            ImportedPortfolioPositionDB(
                id=uuid4().hex,
                user_id=user_id,
                source=SOURCE_NAME,
                symbol=symbol,
                security_name=holding.get("security_name") or _first_trade_name(trade_points),
                current_position=holding.get("current_position"),
                available_position=holding.get("available_position"),
                average_cost=holding.get("average_cost"),
                market_value=market_value,
                current_position_pct=position_pct,
                trade_points_json=trade_points[:20],
                trade_points_count=len(trade_points),
                latest_trade_at=_combine_trade_datetime(latest_trade) if latest_trade else None,
                latest_trade_action=latest_trade.get("action_code") if latest_trade else None,
                last_imported_at=now,
            )
        )

    config = db.query(ThsImportConfigDB).filter(ThsImportConfigDB.user_id == user_id).first()
    if not config:
        config = ThsImportConfigDB(id=uuid4().hex, user_id=user_id)
        db.add(config)
    config.holdings_filename = holdings_filename
    config.trades_filename = trades_filename
    config.auto_apply_scheduled = auto_apply_scheduled
    config.last_imported_at = now
    config.last_error = None

    scheduled_sync = {"created": [], "existing": [], "skipped_limit": []}
    if auto_apply_scheduled:
        ordered_holdings_symbols = [
            row["symbol"]
            for row in holdings_rows
            if (row.get("current_position") or 0) > 0
        ]
        scheduled_sync = scheduled_service.ensure_scheduled_for_symbols(
            db=db,
            user_id=user_id,
            symbols=ordered_holdings_symbols,
        )

    db.commit()
    return get_import_state(db, user_id, scheduled_sync=scheduled_sync)
    


def get_import_state(
    db: Session,
    user_id: str,
    scheduled_sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = db.query(ThsImportConfigDB).filter(ThsImportConfigDB.user_id == user_id).first()
    positions = list_imported_positions(db, user_id)
    return {
        "broker": SOURCE_NAME,
        "holdings_filename": config.holdings_filename if config else None,
        "trades_filename": config.trades_filename if config else None,
        "auto_apply_scheduled": config.auto_apply_scheduled if config else False,
        "last_imported_at": config.last_imported_at.isoformat() if config and config.last_imported_at else None,
        "last_error": config.last_error if config else None,
        "summary": {
            "positions": len(positions),
            "trade_points": sum(item["trade_points_count"] for item in positions),
        },
        "scheduled_sync": scheduled_sync or {
            "created": [],
            "existing": [],
            "skipped_limit": [],
        },
        "positions": positions,
    }


def list_imported_positions(db: Session, user_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(ImportedPortfolioPositionDB)
        .filter(
            ImportedPortfolioPositionDB.user_id == user_id,
            ImportedPortfolioPositionDB.source == SOURCE_NAME,
        )
        .order_by(
            ImportedPortfolioPositionDB.market_value.desc().nullslast(),
            ImportedPortfolioPositionDB.current_position.desc().nullslast(),
            ImportedPortfolioPositionDB.symbol,
        )
        .all()
    )
    return [
        {
            "symbol": row.symbol,
            "name": row.security_name or row.symbol,
            "current_position": row.current_position,
            "available_position": row.available_position,
            "average_cost": row.average_cost,
            "market_value": row.market_value,
            "current_position_pct": row.current_position_pct,
            "trade_points_count": row.trade_points_count or 0,
            "latest_trade_at": row.latest_trade_at,
            "latest_trade_action": row.latest_trade_action,
            "last_imported_at": row.last_imported_at.isoformat() if row.last_imported_at else None,
            "recent_trade_points": list(row.trade_points_json or []),
        }
        for row in rows
    ]


def build_scheduled_user_context(db: Session, user_id: str, symbol: str) -> dict[str, Any]:
    config = db.query(ThsImportConfigDB).filter(ThsImportConfigDB.user_id == user_id).first()
    if not config or not config.auto_apply_scheduled:
        return {}

    row = (
        db.query(ImportedPortfolioPositionDB)
        .filter(
            ImportedPortfolioPositionDB.user_id == user_id,
            ImportedPortfolioPositionDB.source == SOURCE_NAME,
            ImportedPortfolioPositionDB.symbol == (symbol or "").strip().upper(),
        )
        .first()
    )
    if not row:
        return {}

    notes: list[str] = ["来源：本地同花顺导入"]
    trade_points = list(row.trade_points_json or [])
    if trade_points:
        notes.append(f"同花顺历史买卖点（最近 {min(len(trade_points), 8)} 笔）：")
        for point in trade_points[:8]:
            notes.append(_format_trade_point(point))

    payload: dict[str, Any] = {
        "objective": "持有处理" if (row.current_position or 0) > 0 else "观察",
        "current_position": row.current_position,
        "current_position_pct": row.current_position_pct,
        "average_cost": row.average_cost,
        "user_notes": "\n".join(notes) if notes else None,
    }
    return normalize_user_context(payload)


def clear_imported_portfolio(db: Session, user_id: str) -> None:
    db.query(ImportedPortfolioPositionDB).filter(
        ImportedPortfolioPositionDB.user_id == user_id,
        ImportedPortfolioPositionDB.source == SOURCE_NAME,
    ).delete()
    db.query(ThsImportConfigDB).filter(ThsImportConfigDB.user_id == user_id).delete()
    db.commit()


def _parse_holdings_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    df = _read_dataframe(filename, content)
    symbol_col = _find_column(df.columns, HOLDINGS_ALIASES["symbol"])
    if not symbol_col:
        raise ValueError("持仓文件缺少股票代码列，请从同花顺重新导出后再试")

    name_col = _find_column(df.columns, HOLDINGS_ALIASES["security_name"])
    position_col = _find_column(df.columns, HOLDINGS_ALIASES["current_position"])
    cost_col = _find_column(df.columns, HOLDINGS_ALIASES["average_cost"])
    available_col = _find_column(df.columns, HOLDINGS_ALIASES["available_position"])
    market_value_col = _find_column(df.columns, HOLDINGS_ALIASES["market_value"])

    rows: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        symbol = _normalize_stock_code(raw.get(symbol_col))
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "security_name": _clean_text(raw.get(name_col)) if name_col else symbol,
                "current_position": _to_float(raw.get(position_col)) if position_col else None,
                "available_position": _to_float(raw.get(available_col)) if available_col else None,
                "average_cost": _to_float(raw.get(cost_col)) if cost_col else None,
                "market_value": _to_float(raw.get(market_value_col)) if market_value_col else None,
            }
        )
    return rows


def _parse_trades_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    df = _read_dataframe(filename, content)
    symbol_col = _find_column(df.columns, TRADES_ALIASES["symbol"])
    action_col = _find_column(df.columns, TRADES_ALIASES["action"])
    if not symbol_col or not action_col:
        raise ValueError("成交文件缺少股票代码或买卖方向列，请检查同花顺导出内容")

    date_col = _find_column(df.columns, TRADES_ALIASES["trade_date"])
    time_col = _find_column(df.columns, TRADES_ALIASES["trade_time"])
    name_col = _find_column(df.columns, TRADES_ALIASES["security_name"])
    qty_col = _find_column(df.columns, TRADES_ALIASES["quantity"])
    price_col = _find_column(df.columns, TRADES_ALIASES["price"])
    amount_col = _find_column(df.columns, TRADES_ALIASES["amount"])

    rows: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        symbol = _normalize_stock_code(raw.get(symbol_col))
        if not symbol:
            continue
        action_code, action_label = _normalize_trade_action(raw.get(action_col))
        if not action_code:
            continue
        rows.append(
            {
                "symbol": symbol,
                "security_name": _clean_text(raw.get(name_col)) if name_col else symbol,
                "trade_date": _normalize_trade_date(raw.get(date_col)),
                "trade_time": _normalize_trade_time(raw.get(time_col)),
                "action_code": action_code,
                "action_label": action_label,
                "quantity": _to_float(raw.get(qty_col)) if qty_col else None,
                "price": _to_float(raw.get(price_col)) if price_col else None,
                "amount": _to_float(raw.get(amount_col)) if amount_col else None,
            }
        )
    return rows


def _read_dataframe(filename: str, content: bytes) -> pd.DataFrame:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        try:
            df = pd.read_excel(BytesIO(content))
        except Exception as exc:
            raise ValueError(f"暂时无法读取 {suffix} 文件，请优先导出为 CSV/TXT：{exc}") from exc
    else:
        text = _decode_text(content)
        df = _read_delimited_text(text)

    df = df.copy()
    df.columns = [str(col).strip().replace("\ufeff", "") for col in df.columns]
    drop_cols = [col for col in df.columns if col.lower().startswith("unnamed:")]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df.fillna(value=pd.NA)


def _read_delimited_text(text: str) -> pd.DataFrame:
    last_error: Exception | None = None
    df = _try_read_delimited_text(text)
    if df is not None and len(df.columns) > 1:
        return df

    embedded_table = _extract_embedded_table_text(text)
    if embedded_table:
        df = _try_read_delimited_text(embedded_table)
        if df is not None:
            return df

    if last_error is not None:
        raise ValueError(f"无法解析导出文件，请确认是同花顺导出的表格文本：{last_error}") from last_error
    raise ValueError("无法解析导出文件，请确认是同花顺导出的表格文本")


def _try_read_delimited_text(text: str) -> pd.DataFrame | None:
    last_error: Exception | None = None
    for sep in (None, ",", "\t", ";", "|"):
        try:
            if sep is None:
                return pd.read_csv(StringIO(text), sep=None, engine="python")
            return pd.read_csv(StringIO(text), sep=sep)
        except Exception as exc:
            last_error = exc
    return None


def _extract_embedded_table_text(text: str) -> str | None:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    header_markers = (
        "证券代码",
        "股票代码",
        "证券名称",
        "股票名称",
        "成交日期",
        "成交时间",
        "股票余额",
        "成交数量",
    )

    start = None
    separator = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for sep in ("\t", ",", ";", "|"):
            if stripped.count(sep) < 2:
                continue
            if any(marker in stripped for marker in header_markers):
                start = idx
                separator = sep
                break
        if start is not None:
            break

    if start is None or separator is None:
        return None

    table_lines: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if table_lines:
                break
            continue
        if separator not in line:
            if table_lines:
                break
            continue
        table_lines.append(line)

    if len(table_lines) < 2:
        return None
    return "\n".join(table_lines)


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16le", "utf-16be"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin1")


def _find_column(columns: Iterable[Any], aliases: list[str]) -> str | None:
    normalized = {_normalize_column_name(col): str(col) for col in columns}
    for alias in aliases:
        match = normalized.get(_normalize_column_name(alias))
        if match:
            return match
    return None


def _normalize_column_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-:/\\()\[\]（）【】]+", "", text)


def _normalize_stock_code(value: Any) -> str | None:
    text = re.sub(r"[^0-9A-Za-z]", "", str(value or "").strip().upper())
    if not text:
        return None
    if len(text) >= 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return f"{text[2:]}.{text[:2]}"
    if len(text) >= 8 and text[-2:] in {"SH", "SZ", "BJ"} and text[:-2].isdigit():
        return f"{text[:-2]}.{text[-2:]}"
    if text.isdigit():
        if len(text) < 6:
            text = text.zfill(6)
        exchange = _infer_exchange_from_numeric_code(text)
        return f"{text}.{exchange}" if exchange else None
    return None


def _infer_exchange_from_numeric_code(code: str) -> str | None:
    if not code:
        return None
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _normalize_trade_action(value: Any) -> tuple[str | None, str | None]:
    text = str(value or "").strip().upper()
    if not text:
        return None, None
    if any(token in text for token in ("买", "BUY", "B")):
        return "BUY", "买入"
    if any(token in text for token in ("卖", "SELL", "S")):
        return "SELL", "卖出"
    return None, None


def _normalize_trade_date(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if match:
        parts = match.group(1).split("-")
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return text


def _normalize_trade_time(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d{1,2}:\d{1,2}(?::\d{1,2})?)", text)
    if not match:
        return text
    parts = match.group(1).split(":")
    if len(parts) == 2:
        parts.append("00")
    hh, mm, ss = (int(part) for part in parts[:3])
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _combine_trade_datetime(trade: dict[str, Any] | None) -> str | None:
    if not trade:
        return None
    date = trade.get("trade_date")
    time = trade.get("trade_time")
    if date and time:
        return f"{date} {time}"
    return date or time


def _format_trade_point(point: dict[str, Any]) -> str:
    date = point.get("trade_date") or "未知日期"
    action = point.get("action_label") or point.get("action_code") or "交易"
    qty = _format_number(point.get("quantity"))
    price = _format_number(point.get("price"), keep_decimal=True)
    return f"{date} {action} {qty}股 @ {price}"


def _format_number(value: Any, keep_decimal: bool = False) -> str:
    number = _to_float(value)
    if number is None:
        return "0"
    if not keep_decimal and float(number).is_integer():
        return str(int(number))
    return str(float(number))


def _to_float(value: Any) -> float | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("，", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def _clean_text(value: Any) -> str | None:
    if value is None or value is pd.NA:
        return None
    text = str(value).strip()
    return text or None


def _first_trade_name(points: list[dict[str, Any]]) -> str | None:
    for point in points:
        if point.get("security_name"):
            return point["security_name"]
    return None
