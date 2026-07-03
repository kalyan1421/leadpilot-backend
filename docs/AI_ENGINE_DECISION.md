<div align="center">

# 🧠 LeadPilot — AI Engine Selection
### Findings, costs & business impact · **verified 1 July 2026** · rate ₹95/US$

</div>

---

## ⭐ The recommendation in one line

> **Best reasoning we can use → Gemini 3.5 Flash** (score 50/100, beats the pricier Pro), Telugu/Hindi-capable, data in India, **pay in ₹ via UPI**, ~₹**16,200/telecaller/month**.
> **Zero forex risk + cheapest + India-sovereign → Sarvam** (~₹**5,600**) — priced in rupees, immune to the dollar; ideal for lead triage & native summaries.
> **Coming July 2026: Gemini 3.5 Pro** — not released yet; **3.5 Flash already beats today's top Pro**, so nothing to wait for.
> **Avoid premium AIs (₹23k–36k):** for scoring a 2-min call they add **no visible benefit**, are USD-priced, and (Claude/GPT) add 18% GST.

---

## 💱 Currency, billing & forex — "do we have to pay in dollars?"

**No — and this now matters, because the rupee has weakened.** The dollar moved from **~₹85 → ~₹95 in months (≈ +12%)**, so every USD-priced API silently got ~12% costlier in rupees with no change in usage.

| Provider | Billed in | Forex risk | India specifics |
|---|---|:---:|---|
| **Sarvam** | **₹ INR (native)** | ✅ **None** — price fixed in ₹ | India-hosted; **no GST-reverse-charge**, UPI/INR |
| **Gemini (Google)** | **₹ INR via UPI** (no intl card) | ⚠️ **USD-pegged** — ₹ price tracks the dollar | Only Tier-1 AI that bills natively in INR; +~2–3% card forex markup |
| **Claude / GPT (direct)** | **USD** (needs intl card) | ⚠️ USD-pegged | **+18% GST reverse-charge** for Indian companies |

**What it means for us:**
- Want **no forex risk + India-only billing** → **Sarvam** (its ₹ price never moves with the dollar).
- Want **best reasoning + pay in ₹ via UPI** (no international card), accepting dollar-pegged pricing → **Gemini 3.5 Flash**.
- **Claude/GPT** stack international-card + 18% GST *on top of* forex → least attractive for an Indian SMB.

> If the dollar keeps strengthening, the Gemini/Claude/GPT rupee costs below will rise; **Sarvam's will not.**

---

## 📞 The workload we're pricing (per telecaller)

```
   5 hours of calls/day  ·  6 days a week  ·  ~24 working days (Sundays off)
   =  ~120 hours/month  ≈  3,600 calls/month
```
Every call → transcribed → speaker-split → scored → summarised. **Speech-to-text (Sarvam, ₹45/hr, in ₹) is common to every option** — only the AI "brain" differs.

---

## 📅 The Gemini line-up, clarified (verified live)

There is **no "Gemini 3.5 Pro" yet.** Confirmed against Google docs + our live API key (1 Jul 2026):

| Generation | Pro tier | Flash tier |
|---|---|---|
| **3.5** (newest) | ❌ **not released** — *announced, July-2026 target* | ✅ **Gemini 3.5 Flash** (GA, May 2026) |
| 3 / 3.1 | Gemini 3.1 Pro *(preview)* — current top Pro | Gemini 3 Flash *(preview)* |
| 2.5 | Gemini 2.5 Pro (GA) | Gemini 2.5 Flash (GA) |

> "3.1 Pro" **was** the highest Pro that exists — there was no 3.5 Pro to pick. And the newest usable 3.5-gen model, **3.5 Flash, already out-reasons 3.1 Pro (50 vs 46)**.

---

## 🗣️ 1) Indian & mixed-language handling (make-or-break)

| Model | Indian + mixed language | Meaning for us |
|---|---|---|
| **Sarvam** | ★★★★★ Built for Indian languages | Best; writes summaries in natural Telugu |
| **Gemini** (2.5 / 3.5 / Pro) | ★★★★☆ Strong (Google's Indic focus) | Reads Telugu/Hindi + English mix reliably |
| **Claude / GPT-5.x** | ★★★☆☆ Competent, not specialised | Works; Indic isn't their edge |
| **Grok / DeepSeek** | ★★☆☆☆ Weak on Indic | Risky for Telugu/Hindi |

**Safe zone for our market: Sarvam or Gemini.**

---

## 🏆 2) Reasoning rank (Artificial Analysis Intelligence Index, verified)

| Model | Reasoning | Available to us? |
|---|:---:|:---:|
| Claude Fable 5 | 60 | ⛔ export-restricted (non-US) |
| Claude Opus 4.8 | 56 | ✅ |
| GPT-5.5 | 55 | ✅ |
| Claude Sonnet 5 | 53 | ✅ |
| **Gemini 3.5 Flash** | **50** | ✅ ⭐ |
| Gemini 3.1 Pro *(preview)* | 46 | ⚠️ preview (daily cap) |
| Gemini 3 Pro *(preview)* | 40 | ⚠️ preview |
| Grok 4.3 | 38 | ✅ |
| Gemini 2.5 Pro | 26 | ✅ |
| Claude Haiku 4.5 | 24 | ✅ |
| Sarvam 105B | ~18 | ✅ |
| Gemini 2.5 Flash | **14** | ✅ (cheap but weak) |
| *Gemini 3.5 Pro* | *TBD — July 2026* | 🔜 not yet |

> The index measures *hard* reasoning (maths/coding) — **harder than our task**. We don't need a 55-scorer; but the very-low tier (2.5 Flash = 14, Sarvam ≈ 18) shows up as **less consistent rep-scoring**, so we lean to **3.5 Flash (50)** when scores must be defensible.

---

## 💬 3) What each AI's output looks like — & business impact

| Model | Output on our calls | Business impact |
|---|---|---|
| **Sarvam** | Native-Telugu summaries; correct Hot/Warm & BANT *direction*; fine 0–20 scores **wobble** | 💵 Cheapest, ₹-native, India-hosted. Great for **triage + native summaries**; keep fine scores "directional" |
| **Gemini 2.5 Flash** | Clean format, weak reasoning (14) | 🟡 Cheap but barely smarter than Sarvam — little reason to prefer it |
| **Gemini 3.5 Flash** ⭐ | Clean scorecard **+ strong judgment (50)** + accurate evidence quotes | 🏅 **Trustworthy scoring without premium price** — solid enough to coach & reward on |
| **Claude Haiku 4.5** | Reliable output, clean governance; Indic weaker | 🟡 Safe, pricier, USD+GST |
| **Premium** (Opus / GPT-5.5 / Sonnet 5) | Near-flawless | 🔴 No visible gain here; 2–4× cost, USD-priced |

---

## 💰 4) Cost — 1 hour & full month (per telecaller) · **@ ₹95/US$**

All-in = speech-to-text (Sarvam ₹45/hr, in ₹) **+** AI scoring. Real measured usage on our calls.

| Model | Reasoning | ₹ / **1 hour** | ₹ / **month** (120 hrs) | ₹ / **year** | Billing |
|---|:---:|---:|---:|---:|:---:|
| **Sarvam** | ~18 | ₹47 | **₹5,600** | ₹67,000 | 🇮🇳 ₹ native |
| **Gemini 2.5 Flash** | 14 | ₹69 | ₹8,300 | ₹1,00,000 | ₹ via UPI* |
| **Claude Haiku 4.5** | 24 | ₹96 | ₹11,500 | ₹1,38,000 | USD +GST |
| **Gemini 3.5 Flash** ⭐ | **50** | ₹135 | **₹16,200** | ₹1,94,000 | ₹ via UPI* |
| Gemini 3.1 Pro *(preview)* | 46 | ₹165+ | ₹19,800+ | ₹2,37,000+ | ₹ via UPI* |
| GPT-5.5 | 55 | ₹195 | ₹23,400 | ₹2,80,000 | USD +GST |
| Claude Sonnet 5 | 53 | ₹198 | ₹23,800 | ₹2,85,000 | USD +GST |
| Claude Opus 4.8 | 56 | ₹300 | ₹36,000 | ₹4,32,000 | USD +GST |

*\*Gemini: paid in ₹ via UPI, but the price is **dollar-pegged** — it rises if the rupee weakens. Sarvam is the only truly ₹-fixed option.*

---

## 📈 5) Cost as the team grows (monthly, @ ₹95/US$)

| Team | Sarvam (₹-fixed) | **Gemini 3.5 Flash** ⭐ (USD-pegged) | Premium AI (USD-pegged) |
|---|---:|---:|---:|
| 1 telecaller | ₹5,600 | **₹16,200** | ₹36,000 |
| 10 telecallers | ₹56,000 | **₹1.62 lakh** | ₹3.6 lakh |
| 50 telecallers | ₹2.8 lakh | **₹8.1 lakh** | ₹18 lakh |

> At 50 reps: premium AI ≈ **₹18 lakh/month** (and rising with the dollar); our pick ≈ **₹8.1 lakh**; ₹-fixed Sarvam ≈ **₹2.8 lakh**.

---

## 🚫 6) Ruled out — and why

| Option | Reason |
|---|---|
| **DeepSeek** | Cheapest on paper, but **China-hosted** → DPDP legal risk on Indian data |
| **Grok (xAI)** | Weak Telugu/Hindi; no India data region |
| **Claude Fable 5** (#1) | **Not available to us** — US export-control |
| **Gemini 3.1 Pro** | Preview (daily cap), scores lower (46) than 3.5 Flash (50), costs more |
| **Gemini 2.5 Flash** | Reasoning 14 — weaker than Sarvam; little upside |
| **Premium AIs** | 2–4× cost, USD-priced + GST, no visible gain here |

---

## 🛡️ 7) Compliance — DPDP + tax

| Engine | Data in | Forex | GST reverse-charge | Status |
|---|---|:---:|:---:|:---:|
| **Sarvam** | **India** | ✅ none | ✅ none | ✅ Best |
| **Gemini** (India region) | India | ⚠️ USD-pegged | ⚠️ check | ✅ Safe |
| Claude / GPT | US/region | ⚠️ USD | ⚠️ 18% | ⚠️ Friction |
| **DeepSeek** | **China** | — | — | ❌ No |

---

## ✅ 8) The decision

| Priority | Choose | ₹/rep/month | Why |
|---|---|---:|---|
| 🏅 **Best scores we can act on** | **Gemini 3.5 Flash** | ₹16,200 | Strongest usable reasoning (50), Indic, India data, pay in ₹ via UPI |
| 🇮🇳 **No forex risk + cheapest** | **Sarvam** | ₹5,600 | ₹-native (dollar-proof), India-sovereign; scores "directional" |
| ❌ **Not worth it** | Premium AIs | ₹23k–36k | No visible gain, USD-priced + GST |
| 🔜 **Watch** | Gemini 3.5 Pro | TBD | Re-check on July-2026 launch (3.5 Flash already beats 3.1 Pro) |

**Recommended path:** run production on **Gemini 3.5 Flash** for the best defensible scores (pay in ₹ via UPI); use **Sarvam** as the ₹-fixed, forex-proof, lowest-cost engine for high-volume triage and native summaries. If forex/cost predictability is the top priority, **Sarvam alone** is the safest rupee bet. Re-evaluate when **Gemini 3.5 Pro** ships (July 2026).

---

<div align="center">

*Verified 1 July 2026. FX: USD→INR ≈ **94.7** live (BookMyForex/Wise/Xe); costs computed at **₹95/US$**; card payments add ~2–3% forex markup; foreign APIs may attract 18% GST reverse-charge — Sarvam (₹-native) has none. Reasoning: Artificial Analysis Intelligence Index. Costs: real measured token usage on LeadPilot's own calls × current published prices. Gemini 3.5 Pro announced (July-2026 target), not yet released.*

</div>
