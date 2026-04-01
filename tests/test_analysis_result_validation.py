from api.main import _ensure_analysis_result_has_content


def test_accepts_result_with_final_decision_only():
    result = {
        "final_trade_decision": "结论：持有，等待放量突破后再考虑加仓。",
        "market_report": "",
        "news_report": "",
        "fundamentals_report": "",
    }

    _ensure_analysis_result_has_content(result, context_label="single_horizon")


def test_accepts_result_with_any_report_body():
    result = {
        "final_trade_decision": "",
        "market_report": "均线仍然多头发散，短线趋势尚未破坏。",
        "news_report": "",
        "fundamentals_report": "",
    }

    _ensure_analysis_result_has_content(result, context_label="single_horizon")


def test_rejects_result_when_all_core_sections_empty():
    result = {
        "final_trade_decision": "",
        "investment_plan": "",
        "trader_investment_plan": "",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "macro_report": "",
        "smart_money_report": "",
    }

    try:
        _ensure_analysis_result_has_content(result, context_label="single_horizon")
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "single_horizon" in str(exc)
        assert "empty analysis content" in str(exc)

    assert raised is True
