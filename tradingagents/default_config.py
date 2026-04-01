import os

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TA_RESULTS_DIR", "./results"),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings
    "llm_provider": os.getenv("TA_LLM_PROVIDER", "openai"),
    "deep_think_llm": os.getenv("TA_LLM_DEEP", "gpt-4o"),
    "quick_think_llm": os.getenv("TA_LLM_QUICK", "gpt-4o-mini"),
    "backend_url": os.getenv("TA_BASE_URL", "https://api.openai.com/v1"),
    "api_key": os.getenv("TA_API_KEY", ""),
    "xbx_data_dir": os.getenv("TA_XBX_DATA_DIR", r"E:\STOCKDATA"),

    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    
    # Debate and discussion settings
    "max_debate_rounds": int(os.getenv("TA_MAX_DEBATE") or "2"),
    "max_risk_discuss_rounds": int(os.getenv("TA_MAX_RISK") or "1"),
    "max_recur_limit": 100,
    
    # Prompt language control: zh, en, or auto
    "prompt_language": os.getenv("TA_LANGUAGE", "zh"),
    "prompt_language_by_provider": {},
    
    # Provider routing trace logs
    "provider_trace": os.getenv("TA_TRACE", "1").lower() in ("1", "true", "yes", "on"),
    "strict_data_vendors": os.getenv("TA_STRICT_DATA_VENDORS", "1").lower() in ("1", "true", "yes", "on"),

    # Data vendor configuration
    "data_vendors": {
        "core_stock_apis": "cn_xbxdata",
        "technical_indicators": "cn_xbxdata",
        "fundamental_data": "cn_xbxdata",
        "news_data": "cn_xbxdata",
        "cn_market_data": "cn_xbxdata",
    },
    "tool_vendors": {},
}
