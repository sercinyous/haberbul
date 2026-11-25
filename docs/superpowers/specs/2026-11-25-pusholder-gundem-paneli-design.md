# Pusholder Gündem Paneli — Tasarım Belgesi

Tarih: 2026-11-25
Durum: Taslak (onay bekliyor)

## 1. Amaç ve neden

Pusholder'ın X ve Instagram hesapları daha fazla etkileşim almalı. Bunun için ekip:

- son dakika haberlerini kaçırmamalı (yerel ve global en kaliteli kaynaklardan),
- paylaşıldığında ses getirecek konuları erken yakalamalı,
- rakiplerden önce girmeli,
- bu konuları Pusholder diline çevrilmiş, paylaşıma hazır taslaklar olarak almalı.

Sistem bir **içerik robotu + rakip analizi + editör paneli**dir. Robot haberi bulur, puanlar, taslağı yazar; **editör onaylar ve kendisi paylaşır.**

## 2. Hedefler

1. Son dakika haberleri, güvenilir kaynakta çıktıktan sonra en geç birkaç dakika içinde panelde sesli/görsel uyarıyla görünür.
2. Viral olma potansiyeli olan konular 0–100 arası bir skorla sıralanır.
3. Her konu için X (≤280 karakter) ve Instagram (başlık + açıklama + hashtag) taslağı Pusholder dilinde hazırlanır.
4. Panel, her konu için kaynağı, yayılma hızını ve rakiplerin o konuya girip girmediğini gösterir.
5. Rakip hesapların son paylaşım sayısı, ortalama etkileşimi ve en iyi içeriği izlenir.
6. X API maliyeti günlük bir bütçe tavanıyla sınırlanır.
7. Editörün hangi taslağı kullandığı ve o paylaşımın aldığı etkileşim kaydedilir; puanlama ve üslup zamanla bundan öğrenir.

## 3. Hedef dışı (bu sürümde yok)

- **Otomatik paylaşım yok.** Robot hiçbir hesaba kendisi paylaşım yapmaz.
- Görsel, kart, video üretimi yok. Yalnızca metin.
- TikTok, YouTube, Facebook, LinkedIn çıktısı yok (ileride eklenebilir). Bu platformlar *kaynak* olarak taranabilir ama taslak üretilmez.
- Okuyucuya dönük web sitesi/uygulama yok; panel iç araçtır.

## 4. Anti-hedefler (teknik olarak çalışsa bile başarısızlık sayılır)

- **Uydurma veya doğrulanmamış haberin "hazır taslak" olarak sunulması.** Her taslak gerçek bir kaynağa bağlı olmalı, kaynak linki panelde görünmeli. Tek kaynaklı haber "tek kaynak" etiketi taşır.
- Ajans/site metninin kopyalanması (telif). Taslaklar her zaman yeniden yazılır, kaynak belirtilir.
- X'in API dışından kazınması (scraping) — kullanım şartlarına aykırı, hesap/anahtar kapatılma riski.
- X bütçesinin aşılması.
- Editörü gürültüyle boğmak: düşük değerli konuların paneli doldurması, aynı haberin farklı kaynaklardan ayrı ayrı görünmesi.

## 5. Kısıtlar

- **X API:** Mevcut, *kullandıkça öde* (pay-per-use) modelinde. Okunan her tweet maliyet demek. Sürekli geniş arama yapılamaz.
- **Instagram:** Kendi hesap performansı Windsor.ai bağlantısıyla okunabilir. Windsor rakip hesap verisi vermez; rakiplerin Instagram verisi bu sürümde sınırlıdır (bkz. Açık noktalar).
- **Haber ajansları:** Tam metin akışları çoğunlukla ücretli abonelik ister; açık RSS genelde başlık + özet verir.
- **Bakım:** Pusholder kendi yazılımını isteyecek; sistem bir yazılımcının bakımını yapabileceği sadelikte olmalı.

## 6. Yaklaşımlar

| Yaklaşım | Artı | Eksi | Karar |
|---|---|---|---|
| **1. Kendi yazılımımız** (Python toplayıcılar + puanlayıcı + Claude API + web paneli + Postgres) | Puanlama, üslup, bütçe tavanı, panel tamamen Pusholder'a göre; büyütmesi kolay | Bakım için yazılımcı gerekir | **Seçildi** |
| 2. Az kodlu otomasyon (n8n + hazır tablo arayüzü) | Birkaç günde ayağa kalkar, kodsuz değiştirilebilir | Akıllı puanlama ve rakip mantığı zorlanır; panel tasarıma ancak yaklaşır | Elendi |
| 3. Hazır sosyal dinleme yazılımı (Brandwatch, Talkwalker vb.) | Rakip analizi güçlü | Pahalı, Türkiye gündemine zayıf, taslak yazmıyor | Elendi |

Seçim gerekçesi: Pusholder kendi yazılımını tercih etti; çekirdek değer (puanlama + üslup + son dakika hızı) özelleştirme gerektiriyor.

## 7. Kararlaştırılan tasarım

### 7.1 Veri kaynakları

**Son dakika hattı** (her 1–2 dakikada bir):
- Yerel: AA, DHA, İHA ve büyük Türk haber siteleri (RSS / varsa abonelik akışı).
- Global: Reuters, AP, AFP, BBC (RSS / varsa abonelik akışı).
- X: Bu kurumların son dakika hesapları (izleme listesinde).

**Gündem hattı** (birkaç dakikada bir, ücretsiz kaynaklar):
- Google Trends — Türkiye'de o an yükselenler.
- Ekşi Sözlük gündemi.
- YouTube trendleri, Reddit.
- Haber sitelerinin RSS akışları.

**X izleme listesi:** Seçilmiş 100–300 hesap (son dakika hesapları, gazeteciler, rakipler). Geniş arama yapılmaz.

**Kendi performans verisi:** Instagram → Windsor.ai; X → X API (kendi zaman akışı).

Kaynak listesi panelden (Kaynaklar, İzleme listesi sayfaları) yönetilebilir olmalı.

### 7.2 X maliyet kontrolü

- Günlük bütçe tavanı (panelde Ayarlar'dan). Tavana ulaşınca X sorguları durur; sistem ücretsiz kaynaklarla devam eder. Panelde kalan bütçe görünür.
- Önbellek: aynı tweet/konu iki kez parayla çekilmez.
- X'e yalnızca aday konular için gidilir: ücretsiz kaynak bir aday bulur → X'te küçük bir sorguyla (birkaç düzine tweet) yayılma hızı ölçülür.
- Paket destekliyorsa yoklama yerine filtrelenmiş anlık akış (filtered stream) kullanılır.

### 7.3 Konu birleştirme

Farklı kaynaklardan gelen aynı haber tek bir "konu" altında toplanır (tekilleştirme). Konu; başlık, kaynak listesi, ilk görülme zamanı, yayılma verisi ve rakip durumunu taşır.

### 7.4 Puanlama

- **Son dakika:** Skor beklenmez. Güvenilir kaynak katmanından (ajans / büyük global kurum) gelen son dakika anında panele düşer, en üstte kırmızı + sesli uyarı verir. Tek kaynaklıysa "tek kaynak" etiketi taşır; ikinci kaynak gelince etiket kalkar.
- **Viral skor (0–100):** Yayılma hızı (X'te etkileşim/dakika ivmesi), kaynak sayısı ve kalitesi, rakiplerin henüz girmemiş olması, Pusholder kitlesinin geçmişte benzer konulara verdiği tepki. Ağırlıklar başlangıçta elle ayarlanır, sonra "paylaşıldı + etkileşim" verisiyle iyileştirilir.

### 7.5 Taslak üretimi (Pusholder dili)

- Claude API, eşik üstü konular ve tüm son dakikalar için X ve Instagram taslağı yazar.
- Üslup: Pusholder'ın X ve Instagram'daki en çok etkileşim alan son paylaşımları örnek olarak verilir (few-shot). Varsa üslup rehberi de eklenir.
- Global haberler Türkçeye çevrilir ve Türkiye'deki okur için neden önemli olduğu eklenir.
- Kaynak metni kopyalanmaz; yeniden yazılır. Taslak kaynağa bağlı kalır, kaynakta olmayan iddia eklenmez.
- Editörün düzeltmeleri kaydedilir; üslup örnek havuzu zamanla bu düzeltilmiş metinlerle güncellenir.
- "Yeniden yaz" butonu aynı konu için yeni bir taslak üretir.

### 7.6 Rakip analizi

- Rakip X hesapları izleme listesindedir. Her konu için "rakip girdi mi, kim, ne zaman" bilgisi tutulur.
- Rakip tablosu (son 1 saat / gün): paylaşım sayısı, ortalama etkileşim, en iyi içerik, son paylaşım zamanı. Pusholder da aynı tabloda karşılaştırma için yer alır.

### 7.7 Editör paneli

Beyaz, sade, Cloudflare tarzı bir dashboard (tek tema, açık). Onaylanan yerleşim: **liste + detay (B)**.
Görsel taslak: https://claude.ai/artifact/WQWJ38vp8NnkVyMeXYMUqk (v2)

- **Sol menü:** Gündem, Taslaklar, Paylaşılanlar, Rakipler, Performans, Kaynaklar, İzleme listesi, Ayarlar.
- **Üst çubuk:** Arama, son tarama zamanı.
- **Özet şeridi:** Sıcak konu (70+), bekleyen taslak, rakipten önce girilen, X bütçesi (kullanılan / tavan).
- **Son dakika uyarısı:** Listenin en üstünde kırmızı şerit + ses.
- **Sol liste:** Skora göre sıralı konular; skor rozeti, yaş, kaynak özeti, rakip etiketi ("Rakip yok" / "Rakip A girdi"). Filtreler: Tümü, 70+, Rakip girmedi, X, Instagram.
- **Sağ detay:** Başlık + skor; Yayılma (sayı + küçük grafik), Kaynak (linkler), Rakipler kutuları; X / Instagram sekmelerinde taslak (karakter sayacıyla); butonlar: Kopyala, Düzenle, Yeniden yaz, Paylaşıldı olarak işaretle, Atla; altta rakip tablosu.
- Panel sürekli güncellenir (yeni konu geldiğinde sayfa yenilemeden).

### 7.8 Geri besleme döngüsü

"Paylaşıldı olarak işaretle" → sistem ilgili Pusholder paylaşımını (X API / Windsor) bulur ve etkileşimini belirli aralıklarla çeker. Bu veri Performans sayfasını, viral skor ağırlıklarını ve üslup örnek havuzunu besler.

## 8. Teknoloji (kararlaştırılan)

- Backend ve toplayıcılar: Python.
- Veritabanı: Postgres.
- Taslak üretimi: Claude API (Anthropic).
- IG verisi: Windsor.ai; X verisi: Pusholder'ın mevcut X API hesabı.

## 9. Kurucuya bırakılanlar

- Web framework, iş kuyruğu/zamanlayıcı, frontend teknolojisi, gerçek zamanlı güncelleme yöntemi (WebSocket/SSE/yoklama).
- Konu birleştirme yöntemi (metin benzerliği, embedding vb.).
- Viral skorun başlangıç formülü ve ağırlıkları.
- Kullanılacak Claude modeli (hız/maliyet dengesine göre).
- Barındırma (Pusholder sunucusu veya bulut), dağıtım şekli.
- Veritabanı şeması ayrıntıları.

## 10. Gözden geçirme sonrası verilen kararlar (tasarımcının kararı, onayda kontrol edilecek)

1. **Güvenilir kaynak katmanı:** AA, DHA, İHA, Reuters, AP, AFP, BBC ve bunların resmi X hesapları. Tek tek gazeteci hesapları bu katmanda değildir; onlar gündem hattında skorlanır. Katman listesi panelden (Kaynaklar) düzenlenebilir.
2. **"Son dakika" tespiti:** Güvenilir katmandan gelen ve (a) başlığında/kategorisinde "SON DAKİKA", "BREAKING" vb. işaret taşıyan ya da (b) yayımlandığı akışın son dakika akışı olduğu bilinen haber. Belirsizse haber gündem hattında yüksek öncelikle skorlanır, kırmızı uyarı vermez.
3. **Tek kaynaklı haber ve taslak:** Taslak üretilir ama "Doğrulanmadı — tek kaynak" etiketiyle gelir. Kopyala butonu, editör "kaynağı kontrol ettim" kutusunu işaretleyene kadar pasiftir. İkinci kaynak gelince etiket ve kilit kalkar. Böylece 7.5 (tüm son dakikalara taslak) ile 4 (doğrulanmamış haber hazır taslak olarak sunulmaz) çelişmez.
4. **Rakip Instagram verisi v1'de yok.** Rakip analizi ve "rakip girdi mi" bilgisi v1'de yalnızca X üzerinden yapılır. Rakip Instagram (Meta Business Discovery API, ayrı Meta uygulaması + inceleme) ikinci aşamaya kalır.
5. **Aşamalar:**
   - **Aşama 1:** Kaynak toplama (son dakika + gündem), X izleme + bütçe tavanı, konu birleştirme, skor, Claude ile X/IG taslakları, panel (Gündem, Taslaklar, Rakipler [X], Kaynaklar, İzleme listesi, Ayarlar), son dakika uyarısı.
   - **Aşama 2:** "Paylaşıldı" geri besleme döngüsü, Performans sayfası, skor ağırlıklarının veriyle ayarlanması, üslup havuzunun düzeltmelerle güncellenmesi, rakip Instagram.

## 11. Varsayılanlar (Pusholder cevap verene kadar; hepsi Ayarlar'dan değiştirilebilir)

1. **X API:** Günlük bütçe tavanı ayarlanabilir, varsayılan $5. Anlık akış (filtered stream) olmadığı varsayılır; izleme listesi aralıklı sorgulanır, aralık kalan bütçeye göre otomatik genişler/daralır. Akış desteği sonradan eklenebilecek şekilde toplayıcı arayüzü soyut tutulur.
2. **Ajanslar:** Abonelik yok varsayılır; açık RSS akışları kullanılır. Kaynak tanımı, sonradan abonelik akışı (farklı kimlik doğrulama/format) eklenebilecek şekilde eklenti mantığında tasarlanır.
3. **Ekip:** Tek ortak giriş (kullanıcı adı + şifre). Konu sahiplenme ("bu konu bende") ile çift paylaşım önlenir. Panel dışı uyarı kanalı v1'de yok.

Durum: Pusholder "kur" talimatıyla onaylandı (2026-11-25), varsayılanlar yukarıdaki gibi.
