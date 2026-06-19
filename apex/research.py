"""Research + strategy-discovery pipeline — the honest 'learn from the world' loop.

Flow:
  1. (optional) pull market news for context        [needs NEWS_API_KEY]
  2. (optional) have an LLM summarize + propose ideas [needs GEMINI_API_KEY]
  3. ALWAYS validate every candidate by BACKTEST with an out-of-sample check
  4. promote ONLY configurations that survive validation

The hard rule: nothing a news feed or an LLM 'suggests' is ever trusted until it
proves a positive out-of-sample expectancy on historical data. Research generates
hypotheses; the backtest decides what's real. This is how you 'learn from the best'
without getting fooled by confident nonsense.
"""
import logging, os
from .backtest import grid_search, backtest

log = logging.getLogger("apex.research")


# ---- optional enrichment adapters (no-op without keys) ----
class NewsFetcher:
    def __init__(self, cfg=None):
        self.key = os.getenv("NEWS_API_KEY", "")

    def headlines(self, query, n=5):
        if not self.key:
            return []                      # disabled until a key is provided
        import requests
        try:
            r = requests.get("https://newsapi.org/v2/everything",
                             params={"q": query, "pageSize": n, "sortBy": "publishedAt",
                                     "apiKey": self.key, "language": "en"}, timeout=10)
            return [a["title"] for a in r.json().get("articles", [])][:n]
        except Exception as e:
            log.warning("news fetch failed: %s", e)
            return []


class LLMResearcher:
    """Summarize context + propose parameter ideas with Gemini (or any LLM).
    Used for CONTEXT and HYPOTHESES only — never as a buy/sell oracle."""
    def __init__(self, cfg=None):
        self.key = os.getenv("GEMINI_API_KEY", "")

    def summarize(self, headlines):
        if not (self.key and headlines):
            return ""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = ("Summarize the market sentiment from these headlines in 2 lines "
                      "(risk-on/off, key catalysts). Do NOT give trade signals:\n- "
                      + "\n- ".join(headlines))
            return model.generate_content(prompt).text.strip()
        except Exception as e:
            log.warning("LLM summarize failed: %s", e)
            return ""


# ---- the part that actually finds edges: backtest-validated search ----
def discover(df, cfg, min_trades=8):
    """Search the parameter space, validate out-of-sample, return ranked survivors.
    Only configs with positive OOS expectancy are returned — the rest are rejected."""
    if len(df) < 200:
        return {"promoted": [], "reason": "not enough history to validate"}
    split = int(len(df) * 0.6)
    train, valid = df.iloc[:split], df.iloc[split:]
    ranked = grid_search(train, cfg)            # optimise on TRAIN
    survivors = []
    for (f, s), train_stats in ranked:
        c = _clone(cfg, f, s)
        v = backtest(valid, c)                  # grade on UNSEEN data
        if v["trades"] >= min_trades and v["expectancy_r"] > 0 and v["profit_factor"] > 1.0:
            survivors.append({"ema": (f, s),
                              "oos_expectancy_r": round(v["expectancy_r"], 3),
                              "oos_profit_factor": round(v["profit_factor"], 2),
                              "oos_trades": v["trades"]})
    survivors.sort(key=lambda x: x["oos_expectancy_r"], reverse=True)
    return {"promoted": survivors[:3],
            "reason": "validated out-of-sample" if survivors else "no edge survived validation"}


def research_report(df, cfg, symbol):
    news = NewsFetcher(cfg).headlines(f"{symbol} crypto market")
    context = LLMResearcher(cfg).summarize(news)
    found = discover(df, cfg)
    return {"symbol": symbol, "news": news, "context": context, **found}


def _clone(cfg, f, s):
    import copy
    c = copy.copy(cfg); c.ema_fast, c.ema_slow = f, s
    return c
