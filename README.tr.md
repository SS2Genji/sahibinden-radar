# Sahibinden Arbitraj & Fırsat Radarı

[ 🇬🇧 English ](README.md) | [ 🇹🇷 Türkçe ](README.tr.md)

SS2Genji tarafından geliştirilen, ikinci el pazar yerlerinde arbitraj ve ani fiyat düşüşlerini gerçek zamanlı yakalayan masaüstü radar sistemi. Python 3 standart kütüphanesi ve harici bağımlılık barındırmayan yerel Node.js Chrome DevTools Protocol (CDP) istemcisi üzerine inşa edilmiştir. Canlı ilanları istatistiksel fiyat dağılımlarına göre anlık değerlendirir, Türkçe kusur ve hasar bildirimlerini dilbilgisi düzeyinde ayrıştırır ve fırsatları yerel masaüstü terminal arayüzünde sunar.

---

## Mimari genel bakış

Radar, harici web kazıma kütüphanelerine veya üçüncü parti bulut API'lerine ihtiyaç duymadan çalışır. Hata ayıklama portu üzerinden doğrudan mevcut Chromium oturumunuza bağlanarak tarayıcı parmak izini normal kullanıcı oturumuyla birebir aynı tutar.

```
┌────────────────────────────────────────────────────────┐
│                   Chromium (CDP :9222)                 │
└──────────────────────────┬─────────────────────────────┘
                           │ Chrome DevTools Protocol (WebSocket)
┌──────────────────────────▼─────────────────────────────┐
│             CDP Tarayıcı Motoru (scanner.js)           │
│  - Yerel fetch & WebSocket (Sıfır harici paket)        │
│  - DOM öğeleri, ham fiyatlar, konumlar, linkler        │
└──────────────────────────┬─────────────────────────────┘
                           │ JSON Akışı
┌──────────────────────────▼─────────────────────────────┐
│                 Filtreleme Hattı (Pipeline)            │
│  Katman 0: Şehir filtresi                              │
│  Katman 1: SQLite delta takibi (yeni ilan / indirim)   │
│  Katman 2: Türkçe negatif anahtar kelime temizliği     │
│  Katman 3: İstatistiksel arbitraj ve kusur analizi    │
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
┌─────────────▼────────────┐  ┌────────────▼─────────────┐
│   AdaptiveMarketEngine   │  │   HonestDefectExtractor  │
│  - Dinamik Tukey IQR     │  │  - Türkçe cümle NLP      │
│  - Log-sigmoid ağırlık   │  │  - Olumsuzlama analizi   │
│  - Dolandırıcılık tabanı │  │  - İkili durum etiketi   │
└─────────────┬────────────┘  └────────────┬─────────────┘
              │                            │
┌─────────────▼────────────────────────────▼─────────────┐
│             SQLite Deposu & Web Sunucusu               │
│  - Çok sütunlu birleşik indeksleme                     │
│  - Standart kütüphane Python HTTP sunucusu (:8888)     │
│  - Kowalski yay geçişleri & Impeccable tabular sayılar │
└────────────────────────────────────────────────────────┘
```

---

## Hızlı başlangıç

### Gereksinimler

- Python 3.10 veya üzeri (yalnızca standart kütüphane kullanılır, `pip install` gerekmez)
- Node.js 18 veya üzeri (yerel WebSocket ve fetch motorunu kullanır)
- Google Chrome, Chromium veya Brave tarayıcı

### Tek komutla başlatma

Depoyu klonlayıp başlatma betiğini çalıştırmanız yeterlidir:

```bash
git clone https://github.com/SS2Genji/sahibinden-radar.git
cd sahibinden-radar
./start.sh
```

Bu betik sistemdeki ikili dosyaları kontrol eder, 9222 portunda Chrome açık değilse otomatik başlatır, yerel web sunucusunu çalıştırır ve [http://localhost:8888](http://localhost:8888) adresini açar.

### Manuel başlatma

Bileşenleri ayrı ayrı çalıştırmak isterseniz:

1. Chrome tarayıcınızı hata ayıklama portu açık olarak başlatın:
   ```bash
   google-chrome --remote-debugging-port=9222 --user-data-dir="/tmp/radar-chrome" &
   ```

2. Web arayüz sunucusunu başlatın:
   ```bash
   python3 web_server.py
   ```

3. Tarayıcınızda `http://localhost:8888` adresine gidin.

Web arayüzü yerine doğrudan terminal üzerinden arkaplan taraması çalıştırmak için:
```bash
python3 main.py
```

---

## Özel kategori ve ürün takibi ekleme

Radar, her türlü kategori ve ürün aramasını destekler. Üç farklı şekilde özel takip ekleyebilirsiniz:

### 1. Web arayüzü üzerinden (Pro Takip)

Web panelinin üst menüsündeki **Özel Takip Ekle** butonuna tıklayın:

1. **Hedef Tanımı:** Ürünün adı (örneğin: `Nvidia RTX 4080 Ekran Kartı`).
2. **Sahibinden Arama Linki:** Sahibinden üzerinde yaptığınız aramanın tam URL'si. Yeni ilanların önce gelmesi için aramayı tarihe göre azalan (`sorting=date_desc`) sıralayın:
   ```
   https://www.sahibinden.com/ekran-karti?query_text=rtx+4080&sorting=date_desc
   ```
3. **Fiyat Sınırları:** TL cinsinden taban ve tavan beklenti fiyatı (örneğin: `25000` ile `50000`).
4. **İstenmeyen Kelimeler:** Elenmesini istediğiniz virgülle ayrılmış kelimeler (örneğin: `arızalı, tamirli, kutu, mining`).

Panel üzerinden eklenen özel hedefler SQLite veritabanına (`custom_targets` tablosu) kaydedilir ve her döngüde otomatik taranır.

### 2. `presets.json` ile kalıcı takip paketleri tanımlama

Toplu ve kalıcı ürün paketleri için `presets.json` dosyasını düzenleyebilirsiniz:

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

Tanımladığınız yeni paketi devreye almak için `config.json` içerisindeki `active_presets` listesine adını ekleyin:

```json
{
  "active_presets": ["smartphones", "gaming", "laptops", "gpus"]
}
```

### 3. `config.json` ile genel filtreler ve bildirimler

Genel eşikler `config.json` dosyasından yönetilir:

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

- `target_cities`: Alarmları yalnızca belirtilen illere sınırlar. Tüm Türkiye'yi taramak için boş bırakın: `[]`.
- `min_profit_tl`: İlan fiyatı ile hesaplanan temiz piyasa medyanı arasında aranacak minimum TL kâr farkı.
- `min_discount_pct`: Temiz medyanın altında aranacak minimum yüzde iskonto oranı.
- `telegram`: Gerçek bir arbitraj fırsatı veya ani fiyat düşüşü yakalandığında anında bildirim gönderir.

---

## Matematiksel model ve istatistiksel arbitraj algoritmaları

Sistem sabit fiyat listeleriyle çalışmaz. Piyasa değerini parametrik olmayan sıralama istatistikleri, Tukey çitleri ve Türkçe dilbilgisi çözümlemesiyle dinamik olarak belirler.

### 1. Çift aşamalı aykırı değer ve dolandırıcılık budaması (`AdaptiveMarketEngine`)

Geçerli ve sıralanmış $N_{\text{raw}}$ adet geçmiş fiyat verildiğinde, kaba taban sınırı $\text{floor}_0 = \max(100.0, \; 0.35 \cdot \text{Med}_0)$ aşırı düşük yer tutucu ilanları ($1\text{ TL}$, $50\text{ TL}$ gibi) ikili arama yardımıyla $\mathcal{O}(\log N)$ sürede budar; böylece birinci çeyrekliğin ($Q_1$) kirlenmesini engeller. Elde edilen $N_1$ büyüklüğündeki $[lo_1, hi_1)$ aralığında:

$N_1 \ge 4$ olduğunda, Tukey menteşeleri $Q_1, Q_3$ ve çeyrekler açıklığı $\text{IQR} = Q_3 - Q_1$ değerleri $\mathcal{O}(1)$ sürede hesaplanır. Geçmiş gözlemler ikili arama ile $[F_{\text{low}}, F_{\text{high}}]$ aralığına daraltılır:

$$F_{\text{low}} = \max\left(100.0, \; 0.35 \cdot \text{Med}_1, \; Q_1 - 1.5 \cdot \text{IQR}\right), \qquad F_{\text{high}} = \begin{cases} Q_3 + 1.5 \cdot \text{IQR} & \text{IQR} > 0 \text{ ise} \\ 1.85 \cdot \text{Med}_1 & \text{IQR} = 0 \text{ ise} \end{cases}$$

Olası dolandırıcılık ve sembolik fiyat sınıflandırması (`is_scam_or_placeholder`) için dinamik taban, örneklem boyutuna adapte olurken sıfır varyanslı grupları ($\text{IQR} \to 0$) korumak için $\text{IQR}_{\text{eff}} = \max(\text{IQR}, \; 0.25 \cdot \text{Med})$ formülünü kullanır. Bu sayede geçmiş ilanların aynı fiyatta toplandığı durumlarda $\%12$ ile $\%35$ arasındaki meşru fırsatların yanlışlıkla dolandırıcılık olarak elenmesi önlenir:

$$\text{Floor}_{\text{dyn}} = \begin{cases} \max\left(100.0, \; Q_1 - 1.5 \cdot \text{IQR}_{\text{eff}}\right) & N \ge 4 \text{ ise} \\ \max\left(100.0, \; 0.35 \cdot \text{Med}\right) & N < 4 \text{ ise} \end{cases}$$

### 2. Log-sigmoid örneklem doygunluğu ve MAD kararlılık güveni ($C \in [0.0, 1.0]$)

Örneklem güveni, log-lojistik bir örneklem doygunluk eğrisi $S_N(N)$ ile normal dağılımla tutarlı MAD saçılma kararlılık skorunun $S_{\text{MAD}}$ birleşiminden oluşur:

$$S_N(N) = \frac{1}{1 + \exp\left(-1.35 \cdot (\ln N - \ln 2.5)\right)}, \qquad S_{\text{MAD}} = \exp\left(-1.8 \cdot \frac{1.4826 \cdot \text{MAD}}{\text{Med}}\right)$$

$$C = \text{clamp}_{[0.0, 1.0]}\left(S_N(N) \cdot \left(0.35 + 0.65 \cdot S_{\text{MAD}}\right)\right)$$

Bu formül kararlı gruplarda $N = 3$ iken $C \approx 0.54$, $N = 5$ iken $C \approx 0.69$ ve $N = 15$ iken $C \approx 0.89$ güven puanı üretir.

Medyan Mutlak Sapma (MAD), tutarlılık katsayısıyla birlikte $O(N)$ sürede hesaplanır:
$$MAD = \text{median}\left(|X_i - \text{median}(X)|\right) \times 1.4826$$

### 3. İlk yöneten yüklem çözümlemesi (`HonestDefectExtractor`)

Türkçe ilan metinlerinde virgüller çoğunlukla birden fazla ismi tek bir ortak yükleme bağlar (örneğin: *"Kasada ezik, vuruk, çizik yoktur"*) veya karşıt anlamlı iki cümleyi birleştirir (*"Kasada ezik var, çizik yok"*). Virgülü sert bir cümle sonu kabul etmek, sıralı isim listelerinde hatalı kusur alarmlarına yol açar.

`HonestDefectExtractor`, her eşleşmede $\mathcal{O}(1)$ sürede İlk Yöneten Yüklem çözümlemesini uygular:

1. **Cümlecik penceresi dilimleme:**
   Ayrıştırıcı, eşleşme noktasından ileriye doğru sert cümle sınırlarına kadar 65 karakterlik bir pencere inceler:
   ```python
   _HARD_CLAUSE_STOP_RE = re.compile(
       r"[.;!?\n\r]+|\b(?:ama|fakat|lakin|ancak|yaln[ıi]z|haric[iı](?:nde)?|hari[cç]|ve|ayr[ıi]ca)\b"
   )
   ```
   Virgüller kasti olarak sert sınırların dışında tutulmuştur; böylece sıralı isim listeleri ortak yüklemlerine bağlı kalır.

2. **Yüklem sırası karşılaştırması:**
   Pencere içinde ilk olumsuz yüklemin (`yok`, `yoktur`, `değil`, `değişmedi`, `bulunmuyor`) konumu ile ilk olumlu yüklemin (`var`, `vardır`, `mevcut`, `bulunuyor`) konumu karşılaştırılır. Olumsuz yüklem önce geliyorsa ifade temiz sayılır.

3. **Ön-yoksunluk belirteçleri:**
   Cümle içinde geriye doğru 25 karakterlik bir pencere taranarak ön-yoksunluk belirteçleri (`hiçbir`, `asla`, `kesinlikle`) kontrol edilir.

4. **Deterministik durum etiketleri:**
   - `Temiz`: Donanım kusuru veya dolandırıcılık şüphesi taşımayan cihazlar.
   - `Kusurlu / İşlemli`: Fiziksel hasar, parça değişimi veya arıza bildirilen cihazlar.
   - `Dolandırıcılık Riski`: Şüpheli transfer ifadeleri içeren ilanlar (`kapora`, `havale sonrası kargo`, `yurtdışı`).

### 4. Donanım varyantı kümeleme (`MarketArbitrageEngine`)

Akıllı telefon ve bilgisayarlarda fiyatlar donanım varyantına göre değişir. Motor, medyan hesaplamasından önce teknik özellikleri düzenli ifadelerle çıkarır:

- Depolama kapasitesi: 64GB, 128GB, 256GB, 512GB, 1TB
- Apple işlemci jenerasyonu: M1, M2, M3, M4, Pro, Max
- Pil sağlığı yüzdesi: Sağlıklı ($\ge \%85$) ve zayıflamış pil grupları

128GB bir iPhone 15 Pro, asla 1TB model ile aynı kümeye konulmaz; böylece farklı varyantların birbirini yanıltması engellenir.

---

## Arayüz ve tasarım mühendisliği

Yerel web arayüzü `http://localhost:8888` adresinde çalışır. Emil Kowalski'nin hareket tasarımı ilkeleri ve Paul Bakaus'un Impeccable kalite standartları entegre edilmiştir:

- **Yay geçişleri (Spring Easings):** Özel kübik bezier eğrileri (`--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`, `--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1)`), yapay sıçramalar olmadan yumuşak fiziksel geçiş sağlar.
- **Dokunsal buton tepkisi:** İnteraktif elemanlar 160 milisaniyede `:active scale(0.97)` ile anlık basma hissi verir.
- **Doğal modal animasyonları:** Pencereler göze batan `scale(0)` yerine `scale(0.96) -> scale(1)` ve yumuşak opaklık geçişiyle açılır.
- **Tabular sayılar:** Fiyatlar, iskonto etiketleri ve sayaçlar `font-variant-numeric: tabular-nums` ile biçimlendirilmiştir; veri yenilendiğinde düzen kayması yaşanmaz.
- **Yüzey derinliği:** Koyu tema kartları hafif iç kenarlıklar (`inset 0 1px 0 0 rgba(255, 255, 255, 0.05)`) ve çok katmanlı yumuşak gölgeler kullanır.

---

## Bot koruması ve yerel çalışma prensipleri

İlan platformları `requests`, `urllib` veya `axios` gibi standart HTTP isteklerine karşı bot koruması uygular. Radar üç temel yöntemle bu engeli aşar:

1. **Mevcut tarayıcı oturumunu kullanma:**
   Tarayıcı motoru 9222 portu üzerinden doğrudan kullandığınız gerçek Chrome oturumuyla iletişim kurar. TLS parmak izi (JA3/JA4), HTTP/2 ayarları, çerezler ve yerel depolama korunur.

2. **Rastgele zaman aralıkları (Jitter):**
   Arka plan taramalarında istekler arasına 45 ile 75 saniye arasında değişen rastgele gecikmeler eklenir; periyodik bot davranışı önlenir.

3. **Artımlı sorgu mantığı:**
   Yalnızca sisteme yeni düşen veya doğrulanmış bir fiyat düşüşü yaşayan ilanlar veritabanı güncellemesi ve alarm tetikler; gereksiz istek yapılmaz.

---

## Otomasyon testleri

Test paketi; fiyat sıralama istatistiklerini, dolandırıcılık tabanı filtrelerini, Türkçe olumsuzluk ayrıştırmasını, SQLite delta güncellemelerini ve web API yanıtlarını doğrular.

Tüm test paketini çalıştırmak için:

```bash
python3 -m unittest discover -s tests -v
```

28 birim testinin tamamı Python standart test çalıştırıcısıyla 0.8 saniyenin altında tamamlanır.

---

## Yasal ve etik bilgilendirme

Bu yazılım kişisel pazar araştırması ve fiyat karşılaştırması amacıyla sunulmuştur. Otomatik veri çekme işlemleri platformların kullanıcı sözleşmelerine tabi olabilir. Kullanıcılar platform kurallarına uymakla, makul tarama aralıkları belirlemekle ve sunucu yükü oluşturmamakla kendileri yükümlüdür.

---

## Lisans

MIT Lisansı. **SS2Genji** tarafından geliştirilmiştir.
