"""Compute cost of a 1-hour call vs a typical telecall, all-in, across STT providers."""

USD_INR = 83.5

# ---- Measured LLM token usage (from measure_cost.py, real stored rows) ----
# Analysis is TRUNCATED to 55 turns, so it's ~constant regardless of call length.
A_IN, A_OUT = 1671, 3499      # lead analysis
M_IN, M_OUT = 597, 600        # memory bubble
GROQ_IN, GROQ_OUT = 0.59/1e6, 0.79/1e6   # llama-3.3-70b $/token

def llm_cost(ain, aout, min_, mout):
    return (ain+min_)*GROQ_IN + (aout+mout)*GROQ_OUT

llm_truncated = llm_cost(A_IN, A_OUT, M_IN, M_OUT)           # current design
# Full-fidelity 1-hr call (~360 turns) would be chunked ~6x -> ~5x the analysis tokens
llm_full_1hr = llm_cost(A_IN*3, A_OUT*4, M_IN, M_OUT)        # rough: bigger input+output

# ---- STT provider rates (per minute of audio) ----
STT = {
    "Whisper local (CPU)":      0.0,        # $ — free API, but ~real-time CPU compute
    "Groq whisper-v3-turbo":    0.04/60,    # $0.04 / hour audio
    "Groq whisper-large-v3":    0.111/60,   # $0.111 / hour audio
    "OpenAI Whisper API":       0.006,      # $0.006 / min
    "Sarvam Saarika (est.)":    0.40/USD_INR,  # ~Rs 0.40/min -> verify at sarvam.ai
}

def line(label, mins):
    print(f"\n=== {label} ({mins} min audio) ===")
    print(f"  LLM analysis+memory (truncated): Rs {llm_truncated*USD_INR:.3f}")
    if mins >= 30:
        print(f"  LLM full-fidelity (chunked):     Rs {llm_full_1hr*USD_INR:.3f}")
    print(f"  {'STT provider':<26}{'$/min':>9}{'call STT':>12}{'all-in':>12}")
    for name, rate in STT.items():
        stt = rate*mins
        allin = stt + llm_truncated
        print(f"  {name:<26}{rate:>9.4f}  Rs {stt*USD_INR:>8.2f}  Rs {allin*USD_INR:>8.2f}")

line("TYPICAL TELECALL", 2.3)
line("ONE-HOUR CALL", 60)

print("\n" + "="*60)
print("SCALE — 1-hour calls, all-in (STT + LLM truncated), INR")
print(f"  {'volume':>12}{'Sarvam':>14}{'Groq Whisper':>16}")
for n in (1_000, 10_000, 100_000):
    sarvam = (STT['Sarvam Saarika (est.)']*60 + llm_truncated) * USD_INR * n
    groqw  = (STT['Groq whisper-v3-turbo']*60 + llm_truncated) * USD_INR * n
    print(f"  {n:>12,}  Rs {sarvam:>11,.0f}  Rs {groqw:>13,.0f}")
