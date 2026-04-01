from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from stockstats import wrap

from .base import BaseMarketDataProvider
from ..trade_calendar import cn_no_data_reason
from ..xbx_data import load_financial_df, load_notices_df, load_stock_daily_df, normalize_symbol


class CnXbxDataProvider(BaseMarketDataProvider):
    """A-share provider backed by local xbx data files."""

    INDICATOR_DESCRIPTIONS = {
        "close_50_sma": "50 日均线（SMA）：中期趋势指标。",
        "close_200_sma": "200 日均线（SMA）：长期趋势基准。",
        "close_10_ema": "10 日指数均线（EMA）：短期响应更快。",
        "macd": "MACD：趋势与动量综合指标。",
        "macds": "MACD 信号线（Signal）。",
        "macdh": "MACD 柱状图（Histogram）。",
        "rsi": "RSI：衡量超买/超卖的动量指标。",
        "boll": "布林中轨（20 日均线）。",
        "boll_ub": "布林上轨。",
        "boll_lb": "布林下轨。",
        "atr": "ATR：真实波动幅度均值，用于波动与风控。",
        "vwma": "VWMA：成交量加权均线。",
        "mfi": "MFI：资金流量指标。",
    }

    @property
    def name(self) -> str:
        return "cn_xbxdata"

    @staticmethod
    def _slice_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        start_dt = pd.to_datetime(start_date, errors="coerce")
        end_dt = pd.to_datetime(end_date, errors="coerce")
        out = df.copy()
        out = out[(out["trade_date"] >= start_dt) & (out["trade_date"] <= end_dt)]
        return out.reset_index(drop=True)

    @staticmethod
    def _to_stock_csv(df: pd.DataFrame, symbol: str, start_date: str, end_date: str) -> str:
        if df is None or df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        out = df.copy()
        out["Date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
        out["Open"] = out["open"]
        out["High"] = out["high"]
        out["Low"] = out["low"]
        out["Close"] = out["close"]
        out["Volume"] = out.get("volume", pd.Series([None] * len(out)))
        out["Amount"] = out.get("amount", pd.Series([None] * len(out)))
        out["Dividends"] = 0.0
        out["Stock Splits"] = 0.0
        ordered = out[["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Dividends", "Stock Splits"]]
        header = (
            f"# Stock data for {symbol} from {start_date} to {end_date}\n"
            f"# Total records: {len(ordered)}\n"
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
        return header + ordered.to_csv(index=False)

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        normalized = normalize_symbol(symbol)
        df = load_stock_daily_df(normalized)
        df = self._slice_df(df, start_date, end_date)
        return self._to_stock_csv(df, normalized, start_date, end_date)

    def get_indicators(
        self,
        symbol: str,
        indicator: str,
        curr_date: str,
        look_back_days: int,
    ) -> str:
        if indicator not in self.INDICATOR_DESCRIPTIONS:
            raise ValueError(
                f"Indicator {indicator} is not supported. "
                f"Please choose from: {list(self.INDICATOR_DESCRIPTIONS.keys())}"
            )

        normalized = normalize_symbol(symbol)
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=max(look_back_days, 260))
        df = load_stock_daily_df(normalized)
        df = self._slice_df(df, start_dt.strftime("%Y-%m-%d"), curr_date)
        if df.empty:
            return f"No data found for {normalized} for indicator {indicator}"

        ind_df = df.rename(
            columns={
                "trade_date": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )[["date", "open", "high", "low", "close", "volume"]].copy()
        ind_df = ind_df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        ind_df["volume"] = ind_df["volume"].fillna(0)

        ss = wrap(ind_df)
        indicator_series = ss[indicator]

        values_by_date = {}
        for idx, dt_value in enumerate(ind_df["date"]):
            key = pd.to_datetime(dt_value).strftime("%Y-%m-%d")
            value = indicator_series.iloc[idx]
            values_by_date[key] = "N/A" if pd.isna(value) else str(value)

        begin = curr_dt - timedelta(days=look_back_days)
        lines = []
        current = curr_dt
        while current >= begin:
            key = current.strftime("%Y-%m-%d")
            value = values_by_date.get(key, cn_no_data_reason(key))
            if value == "N/A":
                value = cn_no_data_reason(key)
            lines.append(f"{key}: {value}")
            current -= timedelta(days=1)

        return (
            f"## {indicator} 指标值（{begin.strftime('%Y-%m-%d')} 至 {curr_date}）：\n\n"
            + "\n".join(lines)
            + "\n\n"
            + self.INDICATOR_DESCRIPTIONS[indicator]
        )

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        normalized = normalize_symbol(ticker)
        daily_df = load_stock_daily_df(normalized)
        fin_df = load_financial_df(normalized)

        parts = [f"## Fundamentals for {normalized}"]
        if not daily_df.empty:
            row = daily_df.iloc[-1 if not curr_date else daily_df[daily_df["trade_date"] <= pd.to_datetime(curr_date)].index.max()]
            if row is not None and not pd.isna(row["trade_date"]):
                snapshot = {
                    "股票代码": row.get("symbol"),
                    "股票名称": row.get("name"),
                    "交易日期": row.get("trade_date").strftime("%Y-%m-%d"),
                    "收盘价": row.get("close"),
                    "总市值": row.get("total_market_value"),
                    "流通市值": row.get("float_market_value"),
                    "换手率": row.get("turnover_rate"),
                }
                extra_columns = [
                    column for column in daily_df.columns
                    if any(token in column for token in ("TTM", "净流", "资金", "行业"))
                ][:12]
                for column in extra_columns:
                    snapshot[column] = row.get(column)
                parts.append("### Daily Snapshot")
                parts.append(pd.DataFrame([snapshot]).to_markdown(index=False))

        if not fin_df.empty:
            latest = fin_df.head(6).copy()
            preferred_columns = [
                "stock_code",
                "statement_format",
                "report_date",
                "publish_date",
                "B_total_assets@xbx",
                "B_total_liab@xbx",
                "B_total_owner_equity@xbx",
                "R_revenue@xbx",
                "R_total_profit@xbx",
                "R_np@xbx",
                "R_np_atoopc@xbx",
                "C_ncf_from_oa@xbx",
                "C_final_balance_of_cce@xbx",
                "R_basic_eps@xbx",
            ]
            available = [column for column in preferred_columns if column in latest.columns]
            if available:
                parts.append("### Financial Snapshot")
                latest = latest[available].copy()
                for column in ("report_date", "publish_date"):
                    if column in latest.columns:
                        latest[column] = latest[column].dt.strftime("%Y-%m-%d")
                parts.append(latest.to_markdown(index=False))

        if len(parts) == 1:
            return f"## Fundamentals for {normalized}\n\nNo local xbx fundamentals available."
        return "\n\n".join(parts)

    def _financial_statement(self, ticker: str, columns: list[str], title: str) -> str:
        normalized = normalize_symbol(ticker)
        fin_df = load_financial_df(normalized)
        if fin_df.empty:
            return f"## {title} ({normalized})\n\nNo local xbx financial statement available."
        available = [column for column in columns if column in fin_df.columns]
        if not available:
            return f"## {title} ({normalized})\n\nNo matching columns found in local xbx data."
        table = fin_df.head(8)[available].copy()
        for column in ("report_date", "publish_date"):
            if column in table.columns:
                table[column] = pd.to_datetime(table[column], errors="coerce").dt.strftime("%Y-%m-%d")
        return f"## {title} ({normalized})\n\n{table.to_markdown(index=False)}"

    def get_balance_sheet(self, ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
        return self._financial_statement(
            ticker,
            [
                "stock_code",
                "statement_format",
                "report_date",
                "publish_date",
                "B_total_assets@xbx",
                "B_total_liab@xbx",
                "B_total_owner_equity@xbx",
                "B_fixed_asset@xbx",
                "B_intangible_assets@xbx",
                "B_goodwill@xbx",
            ],
            "Balance Sheet",
        )

    def get_cashflow(self, ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
        return self._financial_statement(
            ticker,
            [
                "stock_code",
                "statement_format",
                "report_date",
                "publish_date",
                "C_sub_total_of_ci_from_oa@xbx",
                "C_sub_total_of_cos_from_oa@xbx",
                "C_ncf_from_oa@xbx",
                "C_sub_total_of_ci_from_ia@xbx",
                "C_sub_total_of_cos_from_ia@xbx",
                "C_ncf_from_ia@xbx",
                "C_sub_total_of_ci_from_fa@xbx",
                "C_sub_total_of_cos_from_fa@xbx",
                "C_ncf_from_fa@xbx",
                "C_final_balance_of_cce@xbx",
            ],
            "Cashflow",
        )

    def get_income_statement(self, ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
        return self._financial_statement(
            ticker,
            [
                "stock_code",
                "statement_format",
                "report_date",
                "publish_date",
                "R_revenue@xbx",
                "R_op@xbx",
                "R_total_profit@xbx",
                "R_np@xbx",
                "R_np_atoopc@xbx",
                "R_basic_eps@xbx",
            ],
            "Income Statement",
        )

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        normalized = normalize_symbol(ticker)
        notices = load_notices_df(normalized)
        if notices.empty:
            return f"No news found for {normalized}"

        start_dt = pd.to_datetime(start_date, errors="coerce")
        end_dt = pd.to_datetime(end_date, errors="coerce")
        filtered = notices[
            (notices["notice_date"] >= start_dt)
            & (notices["notice_date"] <= end_dt)
        ].head(20)
        if filtered.empty:
            return f"No news found for {normalized} between {start_date} and {end_date}"

        rows = []
        for _, row in filtered.iterrows():
            title = str(row.get("title", "")).strip()
            notice_date = row.get("notice_date")
            rows.append(f"### {title}")
            rows.append(f"Date: {notice_date.strftime('%Y-%m-%d') if pd.notna(notice_date) else 'N/A'}")
            rows.append("Source: xbx_local_notice_titles")
            rows.append("")
        return f"## {normalized} 公告标题（{start_date} 至 {end_date}）：\n\n" + "\n".join(rows)

    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        return "当前 xbxdata 本地库未接入全市场全球新闻源。"

    def get_insider_transactions(self, symbol: str) -> str:
        return f"当前 xbxdata 本地库未接入 {normalize_symbol(symbol)} 的高管/股东增减持明细。"

    def get_board_fund_flow(self) -> str:
        return "当前 xbxdata 本地库未提供东财口径板块资金流，建议结合本地行业指数与个股资金字段做替代判断。"

    def get_individual_fund_flow(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        df = load_stock_daily_df(normalized)
        if df.empty:
            return f"No local capital flow data found for {normalized}"
        row = df.iloc[-1]
        columns = [
            column for column in df.columns
            if "资金" in column or "净流" in column
        ]
        if not columns:
            return f"No local capital flow columns found for {normalized}"
        snapshot = {"交易日期": row["trade_date"].strftime("%Y-%m-%d")}
        for column in columns[:16]:
            snapshot[column] = row.get(column)
        return f"## Individual Fund Flow ({normalized})\n\n{pd.DataFrame([snapshot]).to_markdown(index=False)}"

    def get_lhb_detail(self, symbol: str, date: str) -> str:
        return f"当前 xbxdata 本地库未接入 {normalize_symbol(symbol)} 的龙虎榜明细。"

    def get_zt_pool(self, date: str) -> str:
        return f"当前 xbxdata 本地库未接入 {date} 的涨停池。"

    def get_hot_stocks_xq(self) -> str:
        return "当前 xbxdata 本地库未接入雪球热股榜。"
