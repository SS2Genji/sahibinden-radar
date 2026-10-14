# Sahibinden Arbitraj & Fırsat Radarı

[ 🇬🇧 English ](README.md) | [ 🇹🇷 Türkçe ](README.tr.md)

Automated second-hand marketplace arbitrage scanner and deal detection radar by SS2Genji. Built with Python 3's standard library and a native Node.js Chrome DevTools Protocol (CDP) worker. Evaluates live marketplace listings against statistical price distributions, extracts Turkish defect and damage clauses, and displays actionable opportunities in a desktop terminal interface.

> [!WARNING]
> **Rate limiting and safety notice:**
> Marketplace anti-bot systems restrict IP addresses that issue frequent automated requests. To keep your access safe and avoid temporary rate limits:
> - Keep the default scanning intervals between 45 and 75 seconds in `config.json`. Do not reduce intervals below 30 seconds.
> - Monitor a focused set of active targets rather than running dozens of concurrent category scans.
> - This software is intended strictly for personal price comparison and fair-use market tracking. Users are responsible for complying with platform terms of service and avoiding server load.

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

- Python 3.10 or newer (uses standard library only, no external dependencies)
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

## Mathematical model and statistical arbitrage algorithms

The analysis pipeline avoids static price lists. It calculates fair market values dynamically using non-parametric order statistics, Tukey fences, and Turkish natural language clause parsing.

### 1. Two-stage in-place outlier and scam trimming (`AdaptiveMarketEngine`)

Given $N_{\text{raw}}$ sorted valid historical prices, a coarse plausibility bound $\text{floor}_0 = \max(100.0, \; 0.35 \cdot \text{Med}_0)$ strips extreme placeholder listings (such as $1\text{ TL}$ or $50\text{ TL}$) in $\mathcal{O}(\log N)$ time via binary search so they cannot corrupt $Q_1$. On the resulting range $[lo_1, hi_1)$ of size $N_1$:

When $N_1 \ge 4$, Tukey hinges $Q_1, Q_3$ and $\text{IQR} = Q_3 - Q_1$ are computed in $\mathcal{O}(1)$ time. Historical observations are narrowed via binary search to $[F_{\text{low}}, F_{\text{high}}]$ where:

$$F_{\text{low}} = \max\left(100.0, \; 0.35 \cdot \text{Med}_1, \; Q_1 - 1.5 \cdot \text{IQR}\right), \qquad F_{\text{high}} = \begin{cases} Q_3 + 1.5 \cdot \text{IQR} & \text{if } \text{IQR} > 0 \\ 1.85 \cdot \text{Med}_1 & \text{if } \text{IQR} = 0 \end{cases}$$

For candidate scam and placeholder classification (`is_scam_or_placeholder`), the dynamic floor adapts to sample size while stabilizing zero-variance cohorts ($\text{IQR} \to 0$) via $\text{IQR}_{\text{eff}} = \max(\text{IQR}, \; 0.25 \cdot \text{Med})$ so legitimate $12\%\text{ to }35\%$ discounts are not misclassified as scams when historical prices are identical:

$$\text{Floor}_{\text{dyn}} = \begin{cases} \max\left(100.0, \; Q_1 - 1.5 \cdot \text{IQR}_{\text{eff}}\right) & \text{if } N \ge 4 \\ \max\left(100.0, \; 0.35 \cdot \text{Med}\right) & \text{if } N < 4 \end{cases}$$

### 2. Log-sigmoid sample saturation and MAD stability confidence ($C \in [0.0, 1.0]$)

Sample confidence combines a log-logistic sample saturation curve $S_N(N)$ with a normal-consistent MAD dispersion stability score $S_{\text{MAD}}$:

$$S_N(N) = \frac{1}{1 + \exp\left(-1.35 \cdot (\ln N - \ln 2.5)\right)}, \qquad S_{\text{MAD}} = \exp\left(-1.8 \cdot \frac{1.4826 \cdot \text{MAD}}{\text{Med}}\right)$$

$$C = \text{clamp}_{[0.0, 1.0]}\left(S_N(N) \cdot \left(0.35 + 0.65 \cdot S_{\text{MAD}}\right)\right)$$

For stable cohorts, this produces $C \approx 0.54$ at $N = 3$, $C \approx 0.69$ at $N = 5$, and $C \approx 0.89 > 0.80$ at $N = 15$.

Streaming Median Absolute Deviation (MAD) is calculated in $O(N)$ time with consistency scaling:
$$MAD = \text{median}\left(|X_i - \text{median}(X)|\right) \times 1.4826$$

### 3. First-governing-predicate negation resolution (`HonestDefectExtractor`)

In Turkish classifieds, commas frequently coordinate multiple nouns governed by a single clause-final predicate (for example, *"Kasada ezik, vuruk, çizik yoktur"*), or splice contrasting clauses (*"Kasada ezik var, çizik yok"*). Treating a comma as a hard clause boundary causes false positives on coordinate lists.

`HonestDefectExtractor` resolves this in $\mathcal{O}(1)$ time per match using First-Governing-Predicate resolution:

1. **Clause window slicing:**
   The parser inspects a forward 65-character window up to hard clause boundaries:
   ```python
   _HARD_CLAUSE_STOP_RE = re.compile(
       r"[.;!?\n\r]+|\b(?:ama|fakat|lakin|ancak|yaln[ıi]z|haric[iı](?:nde)?|hari[cç]|ve|ayr[ıi]ca)\b"
   )
   ```
   Commas are intentionally excluded from hard clause stops so coordinate noun lists remain bound to their shared predicate.

2. **Predicate order evaluation:**
   It compares the position of the first negative predicate (`yok`, `yoktur`, `değil`, `değişmedi`, `bulunmuyor`) against the first affirmative predicate (`var`, `vardır`, `mevcut`, `bulunuyor`). If the negative predicate appears first, the clause is classified as negated.

3. **Pre-nominal privative checking:**
   It checks a backward 25-character window within the same clause for privative modifiers (`hiçbir`, `asla`, `kesinlikle`).

4. **Deterministic binary labels:**
   - `Temiz`: Device has no extracted hardware flaws or scam patterns.
   - `Kusurlu / İşlemli`: Device has reported physical damage, replaced components, or functional defects.
   - `Dolandırıcılık Riski`: Listing mentions suspicious transfer patterns (`kapora`, `havale sonrası kargo`, `yurtdışı`).

### 4. Hardware specification clustering (`MarketArbitrageEngine`)

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
