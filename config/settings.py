"""
Elastic Scanner — Configuration
All tunables in one place. No magic numbers in engine code.
"""

# ─── ATR Trail State Machine ───────────────────────────────────────
ATR_PERIOD = 28
ATR_FACTOR = 5.0

# ─── Compression Detection (BB/KC Squeeze) ─────────────────────────
BB_LEN = 20
BB_MULT = 2.0
KC_MULT = 1.5

# ─── ALMA Momentum ─────────────────────────────────────────────────
ALMA_PERIOD = 9
ALMA_OFFSET = 0.85
ALMA_SIGMA = 6

# ─── Extension Thresholds (in ATR units) ───────────────────────────
EXT_LOW = 1.5       # PREP ceiling — "ideal zone"
EXT_FIRE_MAX = 2.0  # FIRE ceiling
EXT_HIGH = 3.0      # EXTENDED floor — overextended
EXT_ELITE = 1.2     # Elite entry ceiling

# ─── Compression Score Thresholds ──────────────────────────────────
COMP_PREP_MIN = 3       # PREP requires >= 3 of 5 signals
COMP_FIRE_MIN = 2       # FIRE requires >= 2 (recent)
COMP_FIRE_LOOKBACK = 3  # bars to look back for recent compression

# ─── Fresh Flip ────────────────────────────────────────────────────
FRESH_FLIP_BARS = 4     # must have flipped within last N bars (4H = 16 hours)

# ─── Volume ────────────────────────────────────────────────────────
VOL_SURGE_MULT = 1.2    # volume >= 1.2x 20-bar avg
VOL_RV_MULT = 1.25      # relative volume for elite entry (Dashboard v2.5)
VOL_AVG_PERIOD = 20

# ─── Tension (BB Width Ratio) ─────────────────────────────────────
TENSION_COMPRESSED = 0.60   # tensionVal <= this = COIL
TENSION_BUILDING = 1.00     # tensionVal <= this = BUILDING
TENSION_STRETCHED = 1.40    # tensionVal > this = HOT

# ─── Candle Quality ───────────────────────────────────────────────
CANDLE_BODY_MIN = 0.60      # minimum body % of range
CANDLE_CLOSE_ZONE = 0.25    # close must be in top/bottom 25% of range

# ─── Scoring Thresholds (v3.0 — Pine parity) ─────────────────────
# Quality tiers set in scoring.py (ELITE=75, GOOD=55, EARLY=35, HOT=20)
# These are kept for backward compat references only
TIER_ELITE = 90    # 90+ = ELITE tier
TIER_FIRE = 80     # 80-89 = FIRE tier
TIER_PREP = 70     # 70-79 = PREP tier
TIER_SUPPRESS = 70 # <70 suppressed from tier (but quality scoring uses 20+)

# ─── Anti-Chase ──────────────────────────────────────────────────
# Extension beyond this = fully suppressed regardless of score
EXT_HARD_CEILING = 4.0

# ─── ntfy Alert Thresholds ─────────────────────────────────────────
MIN_NTFY_SCORE = 55     # minimum score for ntfy alert (GOOD+ transitions only)

# ─── Quality Gate ──────────────────────────────────────────────────
MIN_PRICE = 15.0
MAX_PRICE = 9999.0       # effectively no ceiling — scan the full universe
MIN_AVG_VOL = 100_000    # lowered from 300K to include SmallCap names

# ─── Universe ──────────────────────────────────────────────────────
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
RUSSELL1000_URL = "https://en.wikipedia.org/wiki/Russell_1000_Index"
SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
SP600_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"

# Fallback tickers if Wikipedia scrape fails
FALLBACK_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "KO", "PEP", "COST", "AVGO", "LLY", "MCD", "TMO",
    "CSCO", "ACN", "ABT", "DHR", "NEE", "TXN", "WFC", "PM", "UPS",
    "COP", "RTX", "HON", "LOW", "ORCL", "QCOM", "INTC", "AMD", "AMAT",
    "CAT", "GS", "BA", "GE", "SBUX",
]

# Major indices — always included in every scan
INDEX_TICKERS = [
    "SPY",    # S&P 500
    "DIA",    # DJIA
    "QQQ",    # NASDAQ Composite (Nasdaq-100 proxy)
    "IWB",    # Russell 1000
    "^GSPC",  # S&P 500 Index
    "^DJI",   # Dow Jones Industrial Average
    "^IXIC",  # NASDAQ Composite Index
    "^NYA",   # NYSE Composite Index
    "^RUI",   # Russell 1000 Index
]

# ─── Sector ETF Map ───────────────────────────────────────────────
# Maps sector names to ETF tickers for sector strength scoring
SECTOR_ETFS = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}

# All sector ETFs — always downloaded alongside universe
SECTOR_ETF_TICKERS = list(SECTOR_ETFS.values())

# ─── Timeframes ────────────────────────────────────────────────────
TIMEFRAMES = {
    "4H": {"interval": "1h", "period": "60d", "resample": "4h"},
    "1D": {"interval": "1d", "period": "1y",  "resample": None},
    "1W": {"interval": "1wk", "period": "2y", "resample": None},
    "1M": {"interval": "1mo", "period": "5y", "resample": None},
}

# ─── Alerts ────────────────────────────────────────────────────────
NTFY_TOPIC = "elastic-scanner"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# ─── Output ────────────────────────────────────────────────────────
TOP_N = 5
OUTPUT_DIR = "output"

DISCLAIMER = "EDUCATIONAL ANALYSIS ONLY. NOT FINANCIAL ADVICE. NOT A RECOMMENDATION TO BUY, SELL, OR HOLD."
