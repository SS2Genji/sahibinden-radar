# Sahibinden Arbitraj & Fırsat Radarı

Automated second-hand marketplace arbitrage scanner and deal detection radar by SS2Genji. Built with Python 3's standard library and a native Node.js Chrome DevTools Protocol (CDP) worker. Evaluates live marketplace listings against statistical price distributions, extracts Turkish defect and damage clauses, and displays actionable opportunities in a desktop terminal interface.

---

## Architecture overview

The radar operates without third-party web scraping frameworks or cloud APIs. It connects directly to an existing Chromium session through the debugging protocol, keeping browser fingerprints identical to standard user sessions.

```
┌────────────────────────────────────────────────────────┐
│                   Chromium (CDP :9222)                 │
└──────────────────────────┬─────────────────────────────┘
                           │ Chrome DevTools Protocol (WebSocket)
┌──────────────────────────▼─────────────────────────────┐
│             CDP Scanner Worker (scanner.js)            │
│  - Native fetch & WebSocket (Zero dependencies)        │
│  - Extracts DOM items, raw prices, locations, URLs     │
└──────────────────────────┬─────────────────────────────┘
                           │ JSON Stream
┌──────────────────────────▼─────────────────────────────┐
│                 Filter Pipeline Engine                 │
│  Tier 0: Turkish city filter                           │
│  Tier 1: SQLite delta tracking (new vs price drop)     │
│  Tier 2: Turkish negative keyword purge                │
│  Tier 3: Statistical arbitrage & defect classification │
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
┌─────────────▼────────────┐  ┌────────────▼─────────────┐
│   AdaptiveMarketEngine   │  │   HonestDefectExtractor  │
│  - Dynamic Tukey IQR     │  │  - Turkish clause NLP    │
│  - Log-sigmoid weight    │  │  - Negation detection    │
│  - Scam floor rejection  │  │  - Binary condition flag │
└─────────────┬────────────┘  └────────────┬─────────────┘
              │                            │
┌─────────────▼────────────────────────────▼─────────────┐
│             SQLite Storage & Web Server                │
│  - Multi-column aggregate indexing                     │
│  - Zero-dependency Python HTTP daemon (:8888)          │
│  - Kowalski spring transitions & Impeccable numbers    │
└────────────────────────────────────────────────────────┘
```

---

## Quickstart

### Prerequisites

- Python 3.10 or newer (uses standard library only, no `pip install` needed)
- Node.js 18 or newer (uses native WebSocket and fetch)
- Google Chrome, Chromium, or Brave browser

### One-command launch

Clone the repository and run the startup script:

```bash
git clone https://github.com/SS2Genji/sahibinden-radar.git
cd sahibinden-radar
./start.sh
```

The script verifies system binaries, launches Chrome on port 9222 if not already running, starts the local dashboard server, and opens [http://localhost:8888](http://localhost:8888).

### Manual startup

If you prefer starting each component manually:

1. Launch Chrome with the remote debugging port enabled:
   ```bash
   google-chrome --remote-debugging-port=9222 --user-data-dir="/tmp/radar-chrome" &
   ```

2. Start the web dashboard:
   ```bash
   python3 web_server.py
   ```

3. Open `http://localhost:8888` in your browser.

To run headless background scans in terminal mode instead of the web dashboard:
```bash
python3 main.py
```

---

## Adding custom targets and categories

The radar supports arbitrary categories, price ranges, and search queries. You can configure targets in three ways:

### 1. Through the web interface (Pro Takip)

Click **Özel Takip Ekle** in the top navigation bar of the web dashboard:

1. **Hedef Tanımı:** Name of the item (for example, `Nvidia RTX 4080 Ekran Kartı`).
2. **Sahibinden Arama Linki:** Full search URL from Sahibinden. Sort the search by date descending (`sorting=date_desc`) so new listings appear first:
   ```
   https://www.sahibinden.com/ekran-karti?query_text=rtx+4080&sorting=date_desc
   ```
3. **Fiyat Sınırları:** Minimum and maximum expected price in TL (for example, `25000` to `50000`).
4. **İstenmeyen Kelimeler:** Comma-separated negative keywords to filter out (for example, `arızalı, tamirli, kutu, mining`).

Custom targets added via the interface are stored in SQLite (`custom_targets` table) and scanned automatically on every cycle.

### 2. Defining target packs in `presets.json`

For permanent tracking packs, edit `presets.json`:

```json
{
  "gpus": {
    "name": "Oyuncu Ekran Kartları",
    "description": "Nvidia RTX 40 ve AMD RX 7000 serisi",
    "targets": [
      {
        "name": "Nvidia RTX 4080",
        "url": "https://www.sahibinden.com/ekran-karti?query_text=rtx+4080&sorting=date_desc",
        "min_price": 25000,
        "max_price": 52000,
        "category": "rtx_4080"
      }
    ],
    "negative_keywords": [
      "arızalı",
      "tamirli",
      "mining",
      "sadece kutu",
      "takas"
    ]
  }
}
```

To activate your new preset, add its identifier to `active_presets` in `config.json`:

```json
{
  "active_presets": ["smartphones", "gaming", "laptops", "gpus"]
}
```

### 3. Filters and notifications in `config.json`

The file `config.json` controls global thresholds:

```json
{
  "cdp_port": 9222,
  "min_profit_tl": 3000,
  "min_discount_pct": 12.0,
  "target_cities": ["istanbul", "ankara", "izmir"],
  "scan_interval_min_sec": 45,
  "scan_interval_max_sec": 75,
  "telegram": {
    "enabled": true,
    "bot_token": "123456789:ABCdefGhIJKlmNoPQRstuVWXyz",
    "chat_id": "987654321"
  }
}
```

- `target_cities`: Restricts alerts to listings from specified provinces. Leave empty `[]` to monitor all cities in Turkey.
- `min_profit_tl`: Minimum difference in TL between the listing price and the calculated clean market median.
- `min_discount_pct`: Minimum percentage discount required below the clean median.
- `telegram`: Dispatches instant notifications when a genuine arbitrage deal or price drop is discovered.

---

## Statistical arbitrage algorithms

The analysis pipeline avoids static price lists. It calculates fair market values dynamically using non-parametric order statistics, Tukey fences, and Turkish natural language clause parsing.

### Adaptive market pricing (`AdaptiveMarketEngine`)

Traditional arithmetic means fail on marketplace listings because low placeholders (1 TL ads) and inflated sellers distort averages. The engine uses order-statistic trimming:

1. **Placeholder pre-trim:**
   The engine removes placeholder entries before computing percentiles:
   $$\text{Floor}_{\text{coarse}} = \max\left(100, 0.35 \times \text{Median}_{\text{raw}}\right)$$
   Binary search index bounds $[lo, hi)$ discard corrupting low listings in $O(\log N)$ time.

2. **Tukey fence trimming:**
   When sample size $N \ge 4$, the engine calculates the first quartile $Q_1$ and third quartile $Q_3$, defining the interquartile range ($IQR = Q_3 - Q_1$). Listings outside the fence are discarded:
   $$[\text{Fence}_{\text{low}}, \text{Fence}_{\text{high}}] = [Q_1 - 1.5 \times IQR, \;\; Q_3 + 1.5 \times IQR]$$

3. **Dynamic scam floor:**
   To catch scam deposits, accessory-only listings, or humor posts:
   $$\text{Dynamic Floor} = \max\left(100 \text{ TL}, \; 0.35 \times \text{Median}_{\text{clean}}\right)$$
   Any listing priced below this boundary is flagged as `is_scam_or_placeholder` and blocked from firing arbitrage alerts.

4. **Log-sigmoid sample confidence scaling:**
   Small cohorts ($N = 3..5$) carry higher statistical variance than mature cohorts ($N \ge 25$). The engine weights confidence through a continuous log-sigmoid function:
   $$S_N(N) = \frac{1}{1 + \exp\left(-k \cdot (\ln(N) - \ln(N_0))\right)}$$
   With steepness $k = 1.35$ and center $N_0 = 2.5$, confidence reaches $0.50$ at $N = 3$, $0.80$ at $N = 15$, and exceeds $0.92$ at $N \ge 30$.

5. **Dispersion penalty:**
   Cohorts with high variance are penalized using the ratio of effective IQR to the median:
   $$\text{Penalty}_{\text{dispersion}} = \min\left(1.0, \; \frac{IQR_{\text{effective}}}{\text{Fair Market Price}}\right)$$
   $$\text{Confidence} = S_N(N) \times \left(1.0 - 0.5 \times \text{Penalty}_{\text{dispersion}}\right)$$

6. **Streaming median absolute deviation (MAD):**
   Dispersion is calculated in $O(N)$ time with consistency scaling:
   $$MAD = \text{median}\left(|X_i - \text{median}(X)|\right) \times 1.4826$$

### Turkish defect extraction (`HonestDefectExtractor`)

Classifying electronics requires distinguishing damaged devices from genuine discounts. The Turkish language uses clause-dependent negation where single-word searches produce false positives.

The extractor uses punctuation-aware clause parsing:

1. **Clause splitting:**
   Text is split on punctuation (`.`, `,`, `;`, `-`) and adversative conjunctions (`ama`, `fakat`, `lakin`, `ancak`, `yalnız`).

2. **Negation vs. affirmation disambiguation:**
   - Phrases containing negative suffixes or negation indicators (`değişen yok`, `çiziksiz`, `hatasız`, `çalışmayan aksamı yoktur`) are resolved as affirmative health claims.
   - Phrases indicating physical repair (`ekran değişti`, `pil değişti`, `kasa ezik`, `arka cam kırık`, `face id çalışmıyor`) extract as defect tokens.

3. **Binary status label:**
   - `Temiz`: Device has no extracted hardware flaws or scam patterns.
   - `Kusurlu / İşlemli`: Device has reported physical damage, replaced components, or functional defects.
   - `Dolandırıcılık Riski`: Listing mentions suspicious transfer patterns (`kapora`, `havale sonrası kargo`, `yurtdışı`).

### Hardware spec clustering (`MarketArbitrageEngine`)

For smartphones and laptops, prices vary significantly by hardware variant. The engine extracts specifications via regular expressions before calculating medians:

- Storage capacity: 64GB, 128GB, 256GB, 512GB, 1TB
- Apple silicon generation: M1, M2, M3, M4, Pro, Max
- Battery health percentage: clustered into high health ($\ge 85\%$) versus depleted battery cohorts

A 128GB iPhone 15 Pro is never benchmarked against a 1TB model, preventing false discount alarms caused by mixed variant cohorts.

---

## Frontend design engineering

The web interface runs locally at `http://localhost:8888`. It incorporates interaction principles from Emil Kowalski and quality guidelines from Paul Bakaus's Impeccable standard:

- **Spring easings:** Custom cubic-bezier easing curves (`--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`, `--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1)`) provide smooth physical motion without artificial bouncy overshoot.
- **Tactile button response:** Interactive controls respond with `:active scale(0.97)` within 160ms.
- **Natural modal transitions:** Dialogs enter using subtle scale (`scale(0.96) -> scale(1)`) and opacity fade, avoiding jarring `scale(0)` pop-ins.
- **Tabular numerals:** Price figures, discount badges, and timestamp counters use `font-variant-numeric: tabular-nums` to eliminate layout shift during background data refreshes.
- **Surface depth:** Dark theme cards use inset light borders (`inset 0 1px 0 0 rgba(255, 255, 255, 0.05)`) with multi-layered soft drop shadows.

---

## Anti-bot mechanics and local execution

Marketplaces deploy automated detection against standard HTTP libraries like `requests`, `urllib`, or `axios`. The radar avoids detection through three structural mechanisms:

1. **Native browser session reuse:**
   The scanner communicates with your actual desktop browser via port 9222. The browser maintains its native TLS fingerprint (JA3/JA4), HTTP/2 settings, cookies, and local storage.

2. **Randomized jitter intervals:**
   Background scanning applies randomized intervals between requests (configurable between 45 and 75 seconds), avoiding fixed periodic patterns.

3. **Incremental delta queries:**
   Only listings that are either brand new or have undergone a verified price drop trigger database updates and alerts, avoiding unnecessary re-querying.

---

## Automated tests

The test suite covers price order statistics, scam floor filtering, Turkish clause normalization, SQLite delta updates, and web server API responses.

To run the complete test suite:

```bash
python3 -m unittest discover -s tests -v
```

All 28 unit tests execute using Python's standard library test runner in under 0.8 seconds.

---

## Legal and ethical notice

This software is provided for personal market research and price comparison purposes. Automated data extraction may be restricted by website terms of service. Users are responsible for complying with target platform guidelines, setting reasonable request intervals, and avoiding server overloads.

---

## License

MIT License. Developed by **SS2Genji**.
