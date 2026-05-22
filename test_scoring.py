"""Quick test: verify scoring recalibration produces B-grade signals from today's data."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from src.scoring import score_signal, score_to_quality, QUALITY_GOOD

# Recreate today's actual signals (from dashboard.json) with raw fields
# These are the fields that classify_signal() and extract_last_bar() produce
test_signals = [
    {
        "ticker": "SSTK", "direction": "BEAR", "bull_count": 1, "ext": 1.8,
        "comp_score": 3, "is_bull_4h": False, "expansion_fire": False,
        "atr_rise": True, "range_expand": False, "vol_ratio": 1.3,
        "alma_stack_bear": False, "alma21_falling": True,
        "alma_stack_bull": False, "alma21_rising": False,
        "rs_vs_spy": -1.0, "rs_vs_qqq": -0.5,
        "close": 16.26, "trail": 17.0, "alma21": 16.5,
        "flip_age": 3, "recent_comp_max": 3, "is_stretched": False,
    },
    {
        "ticker": "CRM", "direction": "BEAR", "bull_count": 0, "ext": 1.5,
        "comp_score": 2, "is_bull_4h": False, "expansion_fire": False,
        "atr_rise": False, "range_expand": False, "vol_ratio": 1.1,
        "alma_stack_bear": True, "alma21_falling": True,
        "alma_stack_bull": False, "alma21_rising": False,
        "rs_vs_spy": -0.5, "rs_vs_qqq": -1.0,
        "close": 180.23, "trail": 185.0, "alma21": 182.0,
        "flip_age": 10, "recent_comp_max": 2, "is_stretched": False,
    },
    {
        "ticker": "MU", "direction": "BULL", "bull_count": 4, "ext": 3.0,
        "comp_score": 2, "is_bull_4h": True, "expansion_fire": True,
        "atr_rise": True, "range_expand": True, "vol_ratio": 1.8,
        "alma_stack_bull": True, "alma21_rising": True,
        "alma_stack_bear": False, "alma21_falling": False,
        "rs_vs_spy": 5.0, "rs_vs_qqq": 4.0,
        "close": 775.89, "trail": 740.0, "alma21": 760.0,
        "flip_age": 15, "recent_comp_max": 2, "is_stretched": False,
    },
    {
        "ticker": "ACN", "direction": "BEAR", "bull_count": 0, "ext": 0.2,
        "comp_score": 2, "is_bull_4h": False, "expansion_fire": False,
        "atr_rise": False, "range_expand": True, "vol_ratio": 1.0,
        "alma_stack_bear": True, "alma21_falling": True,
        "alma_stack_bull": False, "alma21_rising": False,
        "rs_vs_spy": -2.5, "rs_vs_qqq": -3.0,
        "close": 179.36, "trail": 180.0, "alma21": 180.0,
        "flip_age": 8, "recent_comp_max": 3, "is_stretched": False,
    },
    {
        "ticker": "AFRM", "direction": "MIXED", "bull_count": 3, "ext": 2.5,
        "comp_score": 3, "is_bull_4h": True, "expansion_fire": False,
        "atr_rise": False, "range_expand": True, "vol_ratio": 1.4,
        "alma_stack_bull": False, "alma21_rising": True,
        "alma_stack_bear": False, "alma21_falling": False,
        "rs_vs_spy": 1.0, "rs_vs_qqq": 0.5,
        "close": 64.74, "trail": 60.0, "alma21": 62.0,
        "flip_age": 5, "recent_comp_max": 3, "is_stretched": False,
    },
]

print("=" * 70)
print("  SCORING RECALIBRATION TEST")
print(f"  B-grade threshold: {QUALITY_GOOD}+")
print("=" * 70)

b_count = 0
for sig in test_signals:
    scored = score_signal(dict(sig), {})  # pass copy + empty market context
    grade = "A" if scored["score"] >= 75 else "B" if scored["score"] >= 50 else "C" if scored["score"] >= 30 else "D"
    is_b_plus = scored["score"] >= 50
    if is_b_plus:
        b_count += 1
    
    factors = scored.get("factors", {})
    print(f"\n  {scored['ticker']:6} | Score: {scored['score']:3} | Grade: {grade} | Quality: {scored['quality']}")
    print(f"         STRUCT: {scored['struct_score']:2} (atr={factors.get('s_atr',0)} comp={factors.get('s_comp',0)} trend={factors.get('s_trend',0)})")
    print(f"         MOMT:   {scored['mom_score']:2} (exp={factors.get('s_exp',0)} alma={factors.get('s_alma',0)})")
    print(f"         RS:     {scored['rs_score']:2} (rvol={factors.get('s_rvol',0)} rs={factors.get('s_rs',0)})")
    print(f"         PEN:    {scored['penalties']:3} (stretch={factors.get('pen_stretch',0)})")
    print(f"         Setup:  {scored['setup']}")
    marker = " <<<< B-GRADE (in main cockpit)" if is_b_plus else ""
    status = "PASS" if is_b_plus else "SKIP"
    print(f"         [{status}] {grade}-grade{marker}")

print(f"\n{'=' * 70}")
print(f"  RESULT: {b_count}/{len(test_signals)} signals reach B-grade (50+)")
result = "PASS" if b_count >= 2 else "FAIL"
print(f"  [{result}]: Target is at least 1-2 B-grade signals")
print(f"{'=' * 70}")
