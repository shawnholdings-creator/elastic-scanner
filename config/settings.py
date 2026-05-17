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

# ─── Compression Score Thresholds ──────────────────────────────────
COMP_PREP_MIN = 3       # PREP requires >= 3 of 5 signals
COMP_FIRE_MIN = 2       # FIRE requires >= 2 (recent)
COMP_FIRE_LOOKBACK = 3  # bars to look back for recent compression

# ─── Fresh Flip ────────────────────────────────────────────────────
FRESH_FLIP_BARS = 2     # must have flipped within last N bars (4H = 8 hours)

# ─── Volume ────────────────────────────────────────────────────────
VOL_SURGE_MULT = 1.2    # volume >= 1.2x 20-bar avg
VOL_AVG_PERIOD = 20

# ─── Scoring Weights (sum to 1.0) ─────────────────────────────────
W_MTF = 0.30
W_COMPRESSION = 0.25
W_MOMENTUM = 0.20
W_VOLUME = 0.15
W_EXTENSION = 0.10

# ─── Quality Gate ──────────────────────────────────────────────────
MIN_PRICE = 15.0
MAX_PRICE = 500.0
MIN_AVG_VOL = 300_000

# ─── Universe ──────────────────────────────────────────────────────
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Fallback tickers if Wikipedia scrape fails
FALLBACK_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "KO", "PEP", "COST", "AVGO", "LLY", "MCD", "TMO",
    "CSCO", "ACN", "ABT", "DHR", "NEE", "TXN", "WFC", "PM", "UPS",
    "COP", "RTX", "HON", "LOW", "ORCL", "QCOM", "INTC", "AMD", "AMAT",
    "CAT", "GS", "BA", "GE", "SBUX",
]

# ─── Timeframes ────────────────────────────────────────────────────
TIMEFRAMES = {
    "4H": {"interval": "1h", "period": "60d", "resample": "4h"},
    "1D": {"interval": "1d", "period": "1y",  "resample": None},
    "1W": {"interval": "1wk", "period": "2y", "resample": None},
}

# ─── Alerts ────────────────────────────────────────────────────────
NTFY_TOPIC = "elastic-scanner"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# ─── Output ────────────────────────────────────────────────────────
TOP_N = 5
OUTPUT_DIR = "output"
