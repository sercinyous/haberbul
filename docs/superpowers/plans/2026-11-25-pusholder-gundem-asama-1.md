# Haberbul — Aşama 1 Uygulama Planı (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Son dakika ve viral gündemi yakalayan, Pusholder kitlesine uygunluğunu puanlayan, Pusholder dilinde 3 açılı X ve Instagram taslakları yazan, güvenlik kilitleriyle editöre klavyeyle saniyeler içinde paylaştıran servisi kurmak.

**Architecture:** İki süreç, tek Postgres. `worker` (APScheduler) RSS'i paralel tarar, X'i bütçe paylarıyla ve tek OR-sorgularıyla okur, haberleri konulara birleştirir, Claude ile konuyu etiketler, puanlar ve taslak yazar. `web` (FastAPI + Jinja2 + HTMX) paneli sunar; tek bir async Postgres LISTEN dinleyicisi SSE ile tüm editörlere yayın yapar. Dış servisler ince istemci sınıflarının arkasında; testler sahte istemcilerle çalışır. X veya Claude anahtarı yoksa servis RSS ile çalışmaya devam eder.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, HTMX 1.9.12 (CDN), SQLAlchemy 2.x, Alembic, Postgres 16, psycopg 3 (async LISTEN), APScheduler 3.x, httpx, feedparser, rapidfuzz, anthropic SDK, pydantic-settings, pytest, respx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-25-pusholder-gundem-paneli-design.md` (bölüm 10–12). Görsel referans: `docs/panel-mockup.html`.

## Global Constraints

- **Otomatik paylaşım yok.** X/Instagram'a yazan hiçbir çağrı yok. X yalnızca okunur; X'i API dışından kazıma yok.
- Çıktı yalnızca metin, yalnızca X ve Instagram.
- Her taslak kaynağa bağlı; kaynak metni kopyalanmaz; kaynak künyesi (ör. `(AA)`) taslağın sonuna **kodla** eklenir ve 280 sınırına dahildir.
- **Doğrulama** (`Topic.verified`): son `topic_window_hours` içinde, tier 1–2 kaynaklardan **en az 2 farklı kuruluş** (`Source.org`), en az biri tier 1 veya ikisi tier 2; özetleri ≥ %90 aynı olan iki haber tek kuruluş sayılır. Tier 3 doğrulamaya sayılmaz.
- **Kopyala kuralları** (öncelik sırasıyla):
  1. `banned` (aktif yayın yasağı kelimesi eşleşti) → Kopyala **kapalı**, kırmızı "YAYIN YASAĞI" şeridi; açılamaz.
  2. Taslak benzerliği kaynak özetine > %70 → Kopyala kapalı, "Kaynağa çok benziyor — Yeniden yaz".
  3. `sensitive` → bir tık "Hassas içerik — kontrol ettim" onayı sonrası açık.
  4. Doğrulanmamış + tier 1 kaynak var → açık, yumuşak etiket "Tek kaynak (ajans)".
  5. Doğrulanmamış + yalnızca tier 2–3 → bir tık "Kaynağı kontrol ettim" onayı sonrası açık.
  6. Doğrulanmış → açık.
  Onaylar `verified_by`/`sensitive_ack_by` + zaman olarak kaydedilir (giriş adındaki `editor_name`).
- Son dakika uyarısı: tier 1 + son dakika işareti + **ilk görülme** son `breaking_max_age_min` (30) dk ve `published_at` 30 dk'dan eski değil. Tarihi olmayan haber son dakika sayılmaz.
- X bütçesi: `x_daily_budget_usd` (5.0), `x_cost_per_read_usd` (0.005, faturayla doğrulanmalı). Pay: breaking %50, rakip %25, yayılma %25. DB'de **okuma sayısı** tutulur, USD türetilir. Tavan dolunca istisna yok, sessiz durma. Gün sınırı `Europe/Istanbul`.
- Gün/saat hesapları her yerde `Europe/Istanbul`; DB'de UTC timestamptz.
- Panel: tek ortak giriş + editör adı, beyaz tek tema, Türkçe metinler.
- Claude model adları koda yazılmaz: `ANTHROPIC_MODEL` (taslak) ve `ANTHROPIC_FAST_MODEL` (etiketleme ve son dakika hızlı taslağı) ortam değişkeni. Yoksa ilgili adım atlanır, servis çalışır.
- Sırlar yalnızca ortam değişkeninde; `.env` `.gitignore`'da.
- Tek worker süreci çalışır (README'de yazılı).

## Review Focus

1. **Bir RSS kaynağı çökük/yanlış karakter setli/yavaş** → atlanır, `last_error` dolar, diğerleri sürer; Türkçe karakterler bozulmaz. (Task 2: `test_collect_all_skips_failing_source`, `test_windows_1254_feed_decoded`)
2. **AA haberini birebir kopyalayan site "ikinci kaynak" sayılır** → sayılmaz. (Task 3: `test_near_duplicate_summary_counts_as_one_org`)
3. **X bütçesi biter / boş yanıt** → iş durur, rezerv iadesi doğru, gece yarısı İstanbul saatiyle sıfırlanır. (Task 4: `test_refund_unused_reads`, `test_budget_resets_at_istanbul_midnight`)
4. **Taslakta kaynakta olmayan sayı/isim** → panelde sarı vurgulanır. (Task 7: `test_unsupported_number_flagged`)
5. **Yayın yasağı kelimesi olan konu** → Kopyala hiçbir onayla açılmaz. (Task 10: `test_banned_topic_copy_never_enabled`)

---

## Dosya yapısı

```
haberbul/
  pyproject.toml  docker-compose.yml  Dockerfile  .env.example  .gitignore  README.md  alembic.ini  migrations/
  gundem/
    config.py  db.py  models.py  seeds.py  timeutil.py
    sources/rss.py  sources/x.py
    budget.py  breaking.py  topics.py  safety.py  analysis.py  scoring.py
    voice.py  drafting.py  factcheck.py  rivals.py  pipeline.py  worker.py
    web/app.py  web/events.py  web/templates/*.html  web/static/{panel.css,panel.js,alert.mp3}
  seeds/sources.yaml  seeds/watchlist.yaml  seeds/voice_examples.yaml  seeds/sensitive_keywords.yaml
  tests/
```

---

### Task 1: İskelet, ayarlar, modeller

**Files:** Create `pyproject.toml`, `.gitignore`, `.env.example`, `gundem/{__init__,config,db,models,timeutil}.py`, `alembic.ini`, `migrations/`, `tests/conftest.py`, `tests/test_models.py`

**Interfaces — Produces:**
- `Settings`: `database_url: str`, `anthropic_api_key: str|None=None`, `anthropic_model: str|None=None`, `anthropic_fast_model: str|None=None`, `x_bearer_token: str|None=None`, `x_daily_budget_usd: float=5.0`, `x_cost_per_read_usd: float=0.005`, `x_share_breaking: float=0.5`, `x_share_rival: float=0.25`, `x_share_velocity: float=0.25`, `draft_score_threshold: int=60`, `hot_score: int=70`, `list_min_score: int=50`, `breaking_max_age_min: int=30`, `topic_window_hours: int=6`, `topic_similarity: int=82`, `max_drafts_per_cycle: int=5`, `panel_user: str`, `panel_password: str`, `session_secret: str`, `telegram_bot_token: str|None=None`, `telegram_chat_id: str|None=None`, `timezone: str="Europe/Istanbul"`. `get_settings()` (lru_cache). `get_effective_settings(session) -> Settings` (Task 11'de `AppSetting` ile ezilir; burada yalnızca `get_settings()` döner).
- `timeutil.now_utc() -> datetime`, `timeutil.local_today(tz: str) -> date`.
- `session_scope()` context manager.
- Modeller (hepsinde `id` PK, `created_at` timestamptz UTC):
  - `Source(name, org, kind: "rss"|"trends", url, tier: 1|2|3, lang: "tr"|"en", is_breaking_feed: bool, enabled: bool, etag, last_modified, last_ok_at, last_error)`
  - `Item(source_id FK, external_id, title, summary, url, published_at nullable, first_seen_at, lang, is_breaking, topic_id FK nullable)`; unique `(source_id, external_id)`.
  - `Topic(title, lang, first_seen_at, last_seen_at, is_breaking, score:int=0, category:str|None, fit_score:int|None, x_queries: JSON list, x_velocity: float=0, x_velocity_prev: float=0, x_velocity_measured_at, trends_hit: bool=False, org_count:int=0, verified: bool=False, verified_by, verified_at, sensitive: bool=False, sensitive_ack_by, sensitive_ack_at, banned: bool=False, rival_handle, rival_at, rival_text, rival_eng_per_min: float=0, status: "new"|"claimed"|"shared"|"skipped"="new", claimed_by, claimed_at, shared_at, skip_reason, retracted: bool=False, draft_error)`
  - `Draft(topic_id FK, kind: "fast"|"full", x_variants: JSON [{angle, text, truncated, flags:[str]}], ig: JSON {cover, caption, hashtags, slides:[str]}, similarity: float, created_at)` — en yenisi geçerli.
  - `WatchAccount(handle, role: "breaking"|"rival"|"own", tier, enabled)`
  - `RivalPost(handle, tweet_id unique, text, created_at_x, like_count, repost_count, reply_count, quote_count, topic_id FK nullable)`
  - `XSpend(day: date, job: "breaking"|"rival"|"velocity"|"lookup", reads: int)`; unique `(day, job)`.
  - `XCursor(job unique, since_id)`
  - `BanKeyword(phrase, active: bool, note)`
  - `AppSetting(key unique, value)`
  - `Interaction(topic_id FK, editor, action: "view"|"copy"|"edit"|"verify"|"ack"|"claim"|"shared"|"skip"|"split"|"redraft", variant: int|None, text: str|None, reason: str|None, at)`

- [ ] **Step 1: Test yaz** — `conftest.py`: `TEST_DATABASE_URL` (varsayılan `postgresql+psycopg://postgres@localhost/gundem_test`), `PANEL_USER/PANEL_PASSWORD/SESSION_SECRET` test değerleri `monkeypatch.setenv`, her test tablo oluştur/sil, `db` fixture. Testler:
  - `test_settings_defaults`: `x_daily_budget_usd == 5.0`, `x_share_breaking == 0.5`, `draft_score_threshold == 60`, `anthropic_model is None`.
  - `test_item_unique_per_source` → `IntegrityError`.
  - `test_topic_defaults`: `status == "new"`, `verified is False`, `banned is False`.
  - `test_xspend_unique_day_job`.
  - `test_local_today_istanbul`: `2026-11-25T21:30Z` → `date(2026,11,26)`.
- [ ] **Step 2:** `pytest tests/test_models.py -v` → FAIL.
- [ ] **Step 3:** Uygula; `alembic revision --autogenerate -m "initial"`.
- [ ] **Step 4:** PASS; `alembic upgrade head` boş DB'de hatasız.
- [ ] **Step 5:** `git commit -m "Add project skeleton, settings and database models"`

---

### Task 2: RSS toplayıcı ve son dakika tespiti

**Files:** Create `gundem/sources/{__init__,rss}.py`, `gundem/breaking.py`, `gundem/seeds.py`, `seeds/sources.yaml`, `tests/fixtures/*.xml`, `tests/test_rss.py`, `tests/test_breaking.py`

**Interfaces:**
- Consumes: `Source`, `Item`, `timeutil`
- Produces:
  - `RawItem(external_id, title, summary, url, published_at: datetime|None, lang)`
  - `normalize_url(url) -> str` — `utm_*`, `fbclid`, `gclid` parametreleri ve `#` atılır.
  - `fetch_rss(source, client) -> list[RawItem] | None` — `None` = 304 Not Modified. `If-None-Match`/`If-Modified-Since` gönderir, yanıttan `etag`/`last_modified` günceller. `User-Agent` tarayıcı benzeri. **`feedparser.parse(resp.content)`** (bayt). Zaman aşımı 10 sn. `published_at` gelecekteyse `None` yapılır. `external_id` = entry id, yoksa `normalize_url(link)`.
  - `is_breaking(raw, source, first_seen: datetime, now: datetime, max_age_min: int) -> bool` — `tier == 1` ∧ `raw.published_at is not None` ∧ `now - published_at <= max_age_min` ∧ (`is_breaking_feed` ∨ başlıkta büyük/küçük harf duyarsız "son dakika" | "flaş" | "breaking" | "urgent").
  - `collect(sources, session, client, now) -> list[Item]` — kaynakları `ThreadPoolExecutor(max_workers=8)` ile paralel çeker; yalnızca yeni Item'ları ekler (`first_seen_at=now`); hatada `last_error`, başarıda `last_ok_at`.
  - `load_seeds(session)` — `seeds/*.yaml` ada göre upsert, idempotent.

`seeds/sources.yaml`: AA, DHA, İHA, BBC Türkçe, BBC World (tier 1, `org` alanı dolu, son dakika akışları `is_breaking_feed: true`), 4–6 büyük Türk haber sitesi (tier 2), Google Trends TR (`kind: trends`, `https://trends.google.com/trending/rss?geo=TR`, tier 3), Reddit `r/Turkey/.rss` (tier 3). Reuters/AP/AFP RSS'i yok → X üzerinden (Task 4). Uygulayıcı her URL'yi bir kez `curl` ile dener; çalışmayanı `enabled: false` + yorum.

- [ ] **Step 1: Test yaz**
  - `test_fetch_rss_parses_items`; `test_windows_1254_feed_decoded` (ISO-8859-9 kodlu fixture, başlıkta "ğüşıöç" doğru).
  - `test_not_modified_returns_none`: 304 → `None`, `etag` gönderildi.
  - `test_normalize_url_strips_tracking`.
  - `test_future_date_cleared`.
  - `test_collect_dedupes`; `test_collect_all_skips_failing_source`.
  - `test_breaking_tier1_fresh_flagged`; `test_old_item_is_not_breaking` (45 dk); `test_undated_item_not_breaking`; `test_tier2_never_breaking`.
  - `test_load_seeds_idempotent`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS (`pytest tests/test_rss.py tests/test_breaking.py -v`).
- [ ] **Step 5:** `git commit -m "Add parallel RSS collector and breaking-news detection"`

---

### Task 3: Konu birleştirme ve doğrulama

**Files:** Create `gundem/topics.py`, `tests/test_topics.py`

**Interfaces:**
- Consumes: `Item`, `Topic`, `Source`
- Produces:
  - `normalize_title(title) -> str` — Türkçe küçük harf (`İ→i`, `I→ı`), "son dakika/flaş/breaking/video/foto" önekleri, noktalama atılır.
  - `similar(a: str, b: str) -> int` — `rapidfuzz.fuzz.token_sort_ratio`; iki taraftan biri 3 kelimeden kısaysa `0`.
  - `assign_topic(item, session, now) -> Topic | None` — tier 3 kaynaktan gelen Item **yeni konu açmaz**: eşleşen konu varsa ona bağlanır ve `trends_hit=True` (Trends kaynağıysa) yapar, yoksa `None` döner (Item konusuz kalır). Tier 1–2: `last_seen_at` son `topic_window_hours` içinde, aynı dildeki konular arasında `similar >= topic_similarity` en yüksek olana bağlanır, yoksa yeni konu. Ardından `recompute_verification(topic)`.
  - `recompute_verification(topic, session) -> None` — tier 1–2 Item'ları `org`'a göre gruplar; iki kuruluşun özetleri `fuzz.ratio >= 90` ise tek kuruluş sayılır; `org_count` yazılır; Global Constraints'teki kurala göre `verified` (editör onayı `verified_by` varsa `True` kalır).
  - `split_item(item_id, session, now) -> Topic` — Item'ı konusundan çıkarıp yeni konu açar; iki konuda da doğrulama yeniden hesaplanır.

- [ ] **Step 1: Test yaz**
  - `test_normalize_strips_prefix_and_turkish_case`: `"SON DAKİKA: İstanbul'da Deprem!"` → `"istanbulda deprem"`.
  - `test_similar_titles_merge`: AA ve Hürriyet'ten "İstanbul'da 4.2 büyüklüğünde deprem" / "SON DAKİKA İstanbul'da 4,2 büyüklüğünde deprem meydana geldi" → aynı konu.
  - `test_single_word_trend_does_not_swallow_topics`: Trends "deprem" → hiçbir konuya bağlanmaz, yeni konu açılmaz.
  - `test_trend_boosts_matching_topic`: Trends "İstanbul'da 4.2 büyüklüğünde deprem" → `trends_hit is True`.
  - `test_two_orgs_verify`: AA + Hürriyet farklı özet → `verified is True`, `org_count == 2`.
  - `test_same_org_two_feeds_not_verified`: AA son dakika + AA genel → `org_count == 1`.
  - `test_near_duplicate_summary_counts_as_one_org`: Hürriyet özeti AA'nın birebir kopyası → `verified is False`.
  - `test_window_uses_last_seen`; `test_different_language_not_merged`; `test_split_item_creates_new_topic`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Group items into topics with organization-based verification"`

---

### Task 4: X istemcisi ve bütçe payları

**Files:** Create `gundem/budget.py`, `gundem/sources/x.py`, `seeds/watchlist.yaml`, `tests/test_budget.py`, `tests/test_x.py`

**Interfaces:**
- Consumes: `XSpend`, `XCursor`, `WatchAccount`, `Settings`, `timeutil.local_today`
- Produces:
  - `XBudget(session, settings, today: date)`:
    - `cap_reads(job) -> int` = `floor(x_daily_budget_usd / x_cost_per_read_usd * share(job))`; `lookup` işi velocity payından yer.
    - `used_reads(job) -> int`, `total_used_usd() -> float`, `total_cap_usd() -> float`
    - `try_reserve(job, reads) -> bool` — satırı `SELECT ... FOR UPDATE` ile kilitler.
    - `refund(job, reads) -> None`
  - `XTweet(id, author_handle, text, created_at, like_count, repost_count, reply_count, quote_count)`
  - `build_query(terms: list[str], from_handles: list[str] = []) -> str` — `(t1 OR t2 ...)` veya `(from:a OR from:b ...)`, sonuna ` -is:retweet -is:reply (lang:tr OR lang:en)`; boş liste → `ValueError`. Uzunluk > 512 → handle listesi bölünür (`build_queries` liste döndürür).
  - `XClient(bearer, budget, http)`:
    - `search_recent(job, query, since_id=None, max_results=10) -> tuple[list[XTweet], str|None]` — `GET https://api.x.com/2/tweets/search/recent`, `tweet.fields=created_at,public_metrics,author_id`, `expansions=author_id`, `user.fields=username`. Önce `try_reserve(job, max_results)`; `False` → `([], None)` X'e gitmeden. Yanıttan `meta.result_count` kadar kullanılmış sayılır, farkı `refund`. `newest_id` ikinci değer. 429/5xx → `([], None)`, log; tam rezerv iade.
  - `engagement_per_min(tweets, now) -> float` — toplam (like+repost+reply+quote) / max(1, en eski tweet'ten bu yana dakika).
  - `controversy(tweets) -> float` — (reply+quote) / max(1, like).

`seeds/watchlist.yaml`: `Reuters`, `AP`, `AFP`, `AFPTurkce` (role breaking, tier 1); AA/DHA/İHA X hesapları **eklenmez** (RSS'ten geliyor). Rakipler `role: rival`, `enabled: false`, yorumda "Pusholder doldurmalı"; Pusholder kendi hesabı `role: own`.

- [ ] **Step 1: Test yaz**
  - `test_cap_split`: $5 / $0.005 → breaking 500, rival 250, velocity 250.
  - `test_reserve_and_refund_unused_reads`: reserve 10, `result_count=3` → `used_reads("breaking") == 3`.
  - `test_budget_exhausted_blocks_without_http`: respx rotası hiç çağrılmaz.
  - `test_budget_resets_at_istanbul_midnight`: `local_today` ertesi gün → yeniden rezerve edilebilir.
  - `test_build_query_parenthesizes`: `build_query(["deprem","istanbul"])` → `"(deprem OR istanbul) -is:retweet -is:reply (lang:tr OR lang:en)"`.
  - `test_search_parses_metrics_and_newest_id`; `test_rate_limited_refunds_all`.
  - `test_engagement_per_min`: 3 tweet, 600 etkileşim, 10 dk → `60.0`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Add read-only X client with per-job daily budgets"`

---

### Task 5: Güvenlik ve konu analizi

**Files:** Create `gundem/safety.py`, `gundem/analysis.py`, `seeds/sensitive_keywords.yaml`, `tests/test_safety.py`, `tests/test_analysis.py`

**Interfaces:**
- Consumes: `Topic`, `Item`, `BanKeyword`, `normalize_title`
- Produces:
  - `apply_safety(topic, items, session) -> None` — başlık + özetlerde (normalize edilmiş) aktif `BanKeyword.phrase` geçerse `banned=True`; `seeds/sensitive_keywords.yaml` (terör, saldırı, patlama, deprem can kaybı, intihar, istismar, taciz, çocuk, reşit olmayan, cinsel, cenaze...) eşleşirse `sensitive=True`. Her yeni Item'da yeniden çalışır (yasak kelimesi sonradan eklenirse de; Task 9 her döngüde son 24 saatteki konulara uygular).
  - `detect_retraction(item) -> bool` — başlık/özet "yalanladı" | "düzeltme" | "correction" | "retracted" | "asılsız" içerir.
  - `TopicAnalyzer(client, model)`; `analyze(topic, items) -> dict | None` — tek kısa Claude çağrısı (`ANTHROPIC_FAST_MODEL`, `max_tokens=300`, `timeout=15`), yalnızca JSON: `{"category": one of ["unlu","futbol","para_fiyat","egitim_sinav","absurt_komik","ofke_tartisma","teknoloji_oyun","nostalji","siyaset","dunya","diger"], "fit": 0-10, "x_queries": [2-3 kısa anahtar kelime/hashtag öbeği], "sensitive": bool}`. Sonuç `topic.category/fit_score/x_queries` alanlarına; `sensitive` yalnızca `True` ise yazılır (anahtar kelime sonucunu kapatmaz). Hata → `None`, alanlar boş kalır.

- [ ] **Step 1: Test yaz**
  - `test_ban_keyword_marks_banned`; `test_inactive_ban_ignored`; `test_sensitive_keyword_marks_sensitive`.
  - `test_detect_retraction`.
  - `test_analyze_writes_fields` (sahte istemci); `test_analyze_invalid_category_falls_back_to_diger`; `test_analyze_error_returns_none`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Add broadcast-ban and sensitive-topic flags and Claude topic analysis"`

---

### Task 6: Viral skor

**Files:** Create `gundem/scoring.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: `Topic`
- Produces: `score_topic(topic, now) -> int`

```
age_min      = dakika(now - topic.first_seen_at)
fit_n        = (topic.fit_score if not None else 5) / 10
accel_n      = clamp((x_velocity - x_velocity_prev) / max(30, x_velocity_prev), 0, 1)
velocity_n   = min(1, x_velocity / 300)
spread_n     = min(1, org_count / 3)
trends_n     = 1 if trends_hit else 0
rival_n      = 0.5 if rival_handle is None else 0.5 + 0.5*min(1, rival_eng_per_min / 50)
recency_n    = max(0, 1 - age_min / 180)
raw = 0.30*fit_n + 0.15*velocity_n + 0.10*accel_n + 0.15*spread_n + 0.10*trends_n + 0.10*rival_n + 0.10*recency_n
if not verified: raw *= 0.85
score = floor(100*raw + 0.5)
if is_breaking: score = max(score, 90)
```

Rakip girmesi ceza değil talep sinyalidir; panel rakibin metnini gösterip farklı açı önerir.

- [ ] **Step 1: Test yaz**
  - `test_hot_topic`: fit 10, velocity 300, prev 0, org 3, trends, rakip yok, 0 dk, verified → `floor(100*(0.30+0.15+0.10+0.15+0.10+0.05+0.10)+0.5) == 95`.
  - `test_rival_traction_raises_score`: aynı konu, rakip 50 etk/dk → `100`.
  - `test_unverified_discount`: ilk testin unverified hali → `floor(95*0.85... )` hesaplanmış değer `81`.
  - `test_missing_fit_uses_5`.
  - `test_breaking_floor_90`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Add viral score with fit, acceleration, trends and rival demand"`

---

### Task 7: Pusholder dilinde taslaklar

**Files:** Create `gundem/voice.py`, `gundem/drafting.py`, `gundem/factcheck.py`, `seeds/voice_examples.yaml`, `tests/test_drafting.py`, `tests/test_factcheck.py`

**Interfaces:**
- Consumes: `Topic`, `Item`, `Draft`
- Produces:
  - `load_voice_examples() -> list[dict]` — `seeds/voice_examples.yaml` (`platform`, `angle`, `text`, `approved_by`); editör onaylı örnekler (ham etkileşime göre değil). Başlangıçta 6–10 yer tutucu.
  - `source_credit(items) -> str` — en yüksek tier'lı kuruluşun kısa adı, `"(AA)"` biçiminde.
  - `factcheck(text, source_text) -> list[str]` — taslaktaki sayılar (`\d+([.,]\d+)?`) ve büyük harfle başlayan, cümle başı olmayan kelimeler kaynak metinde (normalize) yoksa listeye girer.
  - `source_similarity(text, source_text) -> float` — `fuzz.partial_ratio / 100`.
  - `DraftWriter(client, model, fast_model)`:
    - `write_fast(topic, items, session) -> Draft | None` — yalnızca son dakika için; `fast_model`, tek X metni (`angle: "haber"`), `timeout=15`, `kind="fast"`.
    - `write(topic, items, session, tone: str|None = None) -> Draft | None` — `model`, `timeout=40`, `kind="full"`; `tone in (None,"kisa","esprili","ciddi")`.

Prompt (system, Türkçe) sabitleri: genç, hızlı, merak uyandıran ama abartmayan Pusholder dili; örnekler ilham, kopya değil; **yalnızca verilen kaynak bilgisi, ekleme yok**; kaynak cümlesini kopyalama; global haberi Türkçeye çevir, Türkiye'deki okur için neden önemli olduğunu kaynakta dayanağı varsa söyle; kesin olmayan için "bildirildi/iddia edildi"; ilk cümle tek başına zaman akışında çalışmalı; X metni 120–200 karakter hedef. **Yasak kalıplar:** "ŞOK", "inanamayacaksınız", "dikkat çekti", "gündem oldu", "işte detaylar", tamamı büyük harf başlık, art arda emoji, can kaybı haberinde emoji/ünlem. **Hassas kurallar:** mağdur, çocuk, şüpheli kimliği kaynağın ötesinde verilmez; terör örgütü açıklaması alıntılanmaz; can kaybı yalnızca resmi atıfla (AFAD, valilik); intihar yöntemi yok; kişi/örgüt adlı hashtag yok.
Çıktı JSON: `{"x":[{"angle":"haber","text":...},{"angle":"merak","text":...},{"angle":"sana_etkisi_ya_da_soru","text":...}],"instagram":{"cover":"4-7 kelime","caption":...,"hashtags":[...≤8],"slides":[3-5 kısa metin]}}`.

Kod tarafı kurallar: her X varyantına `" " + source_credit` eklenir; toplam > 280 → bir kez "kısalt" ek mesajıyla yeniden istenir, hâlâ uzunsa metin kelime sınırında kesilir (`…` + künye ≤ 280), `truncated=True`. Her varyanta `flags = factcheck(...)`. `Draft.similarity = max(source_similarity(v.text))`. JSON bozuksa bir kez yeniden; olmazsa `topic.draft_error`. API hatası/zaman aşımı → `topic.draft_error = "Taslak üretilemedi"`, `None`.

- [ ] **Step 1: Test yaz**
  - `test_build_messages_has_sources_and_rules`: system'de "ekleme yok" ve "ŞOK" (yasak listesi) geçer; user'da her `item.url`.
  - `test_write_creates_three_variants_with_credit`: her varyant `"(AA)"` ile biter, `len <= 280`.
  - `test_long_variant_retried_then_truncated`.
  - `test_invalid_json_retried_once`; `test_api_error_sets_draft_error`.
  - `test_fast_draft_single_variant`.
  - `test_tone_passed_to_prompt`: `tone="esprili"` → user mesajında "daha esprili".
  - `test_unsupported_number_flagged`: kaynak "2 kişi yaralandı", taslak "3 ölü" → flags `["3"]`.
  - `test_supported_name_not_flagged`.
  - `test_similarity_high_for_copied_text`: taslak = kaynak cümlesi → `> 0.9`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Write three-angle X and Instagram drafts with fact and copy checks"`

---

### Task 8: Rakip takibi

**Files:** Create `gundem/rivals.py`, `tests/test_rivals.py`

**Interfaces:**
- Consumes: `XClient.search_recent`, `build_queries`, `XCursor`, `RivalPost`, `Topic`, `similar`
- Produces:
  - `poll_rivals(x, session, now) -> int` — etkin `rival` + `own` handle'ları için `build_queries(from_handles=...)`, `job="rival"`, `XCursor("rival").since_id`, `max_results=10`; yeni tweet'ler `RivalPost`; cursor güncellenir. Eklenen sayıyı döndürür.
  - `match_rival_coverage(session, now) -> int` — eşleşmemiş rakip tweet'leri `similar >= topic_similarity - 7` ile konulara bağlar; konuda `rival_handle` boşsa handle, zaman, metin ve `engagement_per_min([tweet], now)` yazılır. Pusholder (`own`) tweet'leri eşleşen konuyu `shared` yapmaz, yalnızca bağlar.
  - `rival_stats(session, since) -> list[dict]` — `{"handle","posts","avg_engagement","best_text","last_post_at"}`, `own` dahil.

- [ ] **Step 1: Test yaz** — `test_poll_uses_single_or_query`, `test_poll_updates_cursor`, `test_rival_tweet_marks_topic`, `test_first_rival_wins`, `test_rival_stats_avg` (100 ve 300 → 200).
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Track rival X accounts with one combined query"`

---

### Task 9: İş hattı ve zamanlayıcı

**Files:** Create `gundem/pipeline.py`, `gundem/worker.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 2–8 `Produces`
- Produces:
  - `Deps(session_factory, http, x: XClient|None, analyzer: TopicAnalyzer|None, writer: DraftWriter|None, notifier: Callable[[Topic, Draft|None], None]|None, settings)`
  - `notify(session, type: str, topic_id: int)` — `SELECT pg_notify('gundem', json)`; commit sonrası teslim edilir.
  - `run_breaking_cycle(deps, now) -> list[Topic]` — tier 1 RSS kaynakları; `assign_topic`, `apply_safety`, `detect_retraction` (paylaşılmış konuda `retracted=True` + `notify("retraction")`); yeni son dakika konusu için hemen `writer.write_fast` ve `notify("breaking")`, `notifier` varsa çağrılır.
  - `run_x_breaking_cycle(deps, now)` — `watchlist role=breaking` için `search_recent(job="breaking", since_id=XCursor("breaking"))`; her tweet bir `Item` olur (kaynak: o handle için otomatik oluşturulan `Source(kind="rss", url="x:"+handle, tier=1, org=handle)`), aynı akıştan geçer.
  - `run_gundem_cycle(deps, now) -> int` — tier 2–3 RSS.
  - `run_rival_cycle(deps, now)` — `poll_rivals`, `match_rival_coverage`.
  - `run_velocity_cycle(deps, now)` — `score >= draft_score_threshold`, son dakika değil, `x_queries` dolu, `x_velocity_measured_at` boş veya 30 dk'dan eski en fazla 3 konu; `search_recent(job="velocity", build_query(topic.x_queries), max_results=10)`; `x_velocity_prev = x_velocity`, yeni `x_velocity`.
  - `run_analysis_and_score_cycle(deps, now)` — analiz edilmemiş tier 1–2 konular (en fazla 10/döngü, son dakika önce) için `analyzer.analyze`; son 24 saatteki tüm aktif konular `apply_safety` + `score_topic` ile yeniden puanlanır (zamanla düşüş); değişen skor `notify("topic")`.
  - `run_draft_cycle(deps, now) -> int` — sıra: son dakika (full taslağı yok) → skora göre; şart: `score >= draft_score_threshold` ∨ `is_breaking`, `banned` değil, `draft_error` boş; döngü başı en fazla `max_drafts_per_cycle`; `notify("draft")`.
  - `worker.main()` — `load_seeds`; `Deps` kurulur (anahtar yoksa ilgili alan `None`); APScheduler aralıkları: breaking RSS 60 sn, X breaking 90 sn, gündem RSS 300 sn, rakip 900 sn, velocity 600 sn, analiz+skor 60 sn, draft 30 sn. Hepsi `max_instances=1`, `coalesce=True`; her iş gövdesi try/except + log. `get_effective_settings` her döngüde okunur.

- [ ] **Step 1: Test yaz** (respx RSS, sahte X/analyzer/writer/notifier)
  - `test_breaking_cycle_fast_drafts_and_notifies`.
  - `test_x_breaking_tweet_becomes_item`.
  - `test_draft_cycle_breaking_first_and_capped`: 7 uygun konu, cap 5 → 5 taslak, son dakika dahil.
  - `test_banned_topic_gets_no_draft`.
  - `test_cycle_runs_without_keys`.
  - `test_velocity_cycle_max_3_and_sets_prev`.
  - `test_retraction_flags_shared_topic`.
  - `test_rescore_decays_over_time`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Wire collection, analysis, scoring and drafting into scheduled jobs"`

---

### Task 10: Panel — giriş, Gündem, konu işlemleri, klavye

**Files:** Create `gundem/web/{__init__,app}.py`, `gundem/web/templates/{base,login,gundem,_topic_row,_topic_detail}.html`, `gundem/web/static/{panel.css,panel.js}`, `tests/test_web.py`

**Interfaces:**
- Consumes: modeller, `rival_stats`, `XBudget`, `DraftWriter`, `split_item`, `recompute_verification`
- Produces (rotalar; `/login`, `/health` hariç oturumlu):
  - `GET/POST /login` (`user`, `password`, `editor_name`) → `itsdangerous` imzalı çerez.
  - `GET /` — özet şeridi (sıcak konu: son 1 saat `score >= hot_score`; bekleyen taslak; **kaynaktan kopyaya ortalama süre** bugün; X bütçesi kullanılan/tavan USD), son dakika şeridi, sol liste (son 6 saat, `score >= list_min_score` ∨ `is_breaking`, `status != skipped`; 3 saatten eski ve hiç 70'i geçmemiş olanlar "Eski" katlanır bölümde), filtreler `?f=all|hot|norival|rival`, ilk konunun detayı. `?partial=list` yalnızca listeyi döndürür.
  - `GET /topics/{id}` — detay: başlık, skor, kategori; kutular: Yayılma (sayı + ivme), Kaynaklar (her Item linki + kuruluş + tier), Rakip (handle, süre, **rakibin metni**); "kaynaktan bu yana N dk"; X sekmesinde 3 varyant kartı (açı adı, metin, `n/280`, `flags` sarı vurgulu), Instagram sekmesinde kapak + açıklama + hashtag + slaytlar; kaynak özeti yan panelde. Kopyala durumu Global Constraints'teki kural tablosuna göre (`copy_state(topic, draft) -> Literal["open","soft_single","needs_verify","needs_ack","blocked_ban","blocked_similar"]` saf fonksiyon, `app.py` içinde).
  - `POST /topics/{id}/claim` — koşullu UPDATE; başkasındaysa 409 "Bu konu {ad} tarafından alındı".
  - `POST /topics/{id}/copy` (`variant`, `platform`) — `Interaction(copy)`; konu sahipsizse otomatik claim. Kopyalama tarayıcıda `navigator.clipboard`; bu uç yalnızca kayıt.
  - `POST /topics/{id}/verify` → `verified=True`, `verified_by/at`. `POST /topics/{id}/ack` → `sensitive_ack_by/at`.
  - `POST /topics/{id}/edit` (`variant`, `text`) → `Interaction(edit, text)`.
  - `POST /topics/{id}/shared` → `status="shared"`, `shared_at`.
  - `POST /topics/{id}/skip` (`reason in eski|ilgisiz|zaten_paylastik|yanlis_birlesmis`) → `status="skipped"`, `skip_reason`.
  - `POST /topics/{id}/redraft` (`tone` opsiyonel) → yeni taslakla detay parçası.
  - `POST /items/{id}/split` → `split_item`; liste yenilenir.
  - `GET /health` → `{"db":"ok"}`.
  - Tüm eylemler `Interaction` satırı yazar.

`panel.js` klavye: `J/K` konu, `1/2/3` varyant seç, `C` seçili varyantı kopyala, `I` IG metnini (açıklama + hashtag) kopyala, `E` düzenle, `R` yeniden yaz, `S` paylaşıldı, `X` atla (tek tuşla neden: `1` eski `2` ilgisiz `3` zaten paylaştık `4` yanlış birleşmiş), `/` arama, `?` kısayol listesi. Kopyalama `navigator.clipboard.writeText` + başarısızsa metni seçme. Görsel: `docs/panel-mockup.html` v2 CSS'i ve yerleşimi.

- [ ] **Step 1: Test yaz** (FastAPI `TestClient`)
  - `test_requires_login`; `test_login_wrong_password_rejected`.
  - `test_list_breaking_first_and_hides_low_scores`.
  - `test_second_claim_is_rejected`.
  - `test_copy_state_matrix`: 6 durumun her biri için `copy_state` beklenen değer.
  - `test_banned_topic_copy_never_enabled`: `verify` ve `ack` sonrası da `blocked_ban`.
  - `test_soft_single_source_tier1_copy_open`.
  - `test_copy_records_interaction_and_claims`.
  - `test_skip_requires_valid_reason`.
  - `test_flags_rendered_highlighted`: detayda `class="flag"` içinde `3`.
  - `test_split_item_route`.
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Add editor panel with keyboard workflow, copy locks and topic actions"`

---

### Task 11: Canlı güncelleme, uyarılar, yönetim sayfaları

**Files:** Create `gundem/web/events.py`, `gundem/web/static/alert.mp3`, `gundem/web/templates/{taslaklar,paylasilanlar,rakipler,kaynaklar,izleme,ayarlar}.html`, `tests/test_admin_pages.py`, `tests/test_events.py`; Modify `gundem/web/app.py`, `gundem/config.py`, `gundem/web/static/panel.js`

**Interfaces:**
- Consumes: `pg_notify('gundem')` (Task 9), `rival_stats`, `BanKeyword`, `AppSetting`
- Produces:
  - `EventHub` (`events.py`): uygulama açılışında tek async psycopg bağlantısıyla `LISTEN gundem`; her SSE istemcisi için `asyncio.Queue`; `GET /events` `event: <type>\ndata: <json>\n\n`, 20 sn'de `: ping`, başlıklar `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  - `panel.js`: `EventSource('/events')`; `open` (yeniden bağlanma dahil) ve `topic|draft` → `?partial=list` yenile; `breaking` → kırmızı şerit, `alert.mp3` (ilk tıklamadan sonra; "Sesli uyarıyı aç" butonu), **tarayıcı bildirimi** (`Notification.requestPermission` butondan), sekme başlığında okunmamış sayısı `"(3) Gündem"`; `retraction` → kırmızı "Kaynak düzeltti — paylaşımı kontrol et" şeridi.
  - `GET /taslaklar` (taslaklı, `new|claimed`), `GET /paylasilanlar` (`shared`, `retracted` olanlar kırmızı).
  - `GET /rakipler` — `rival_stats` son 1 saat ve bugün (İstanbul).
  - `GET/POST /kaynaklar` — ekle (ad, kuruluş, url, tier, dil, son dakika akışı), aç/kapat, `last_ok_at`/`last_error`.
  - `GET/POST /izleme` — handle ekle (rol, tier), aç/kapat.
  - `GET/POST /ayarlar` — `x_daily_budget_usd`, `x_cost_per_read_usd`, `x_share_*` (toplam 1.0 olmalı), `draft_score_threshold`, `hot_score`, `list_min_score`; **yayın yasağı kelimeleri** (ekle, pasifleştir, not). `get_effective_settings(session)` ortam değerini `AppSetting` ile ezer.

- [ ] **Step 1: Test yaz**
  - `test_add_source_then_listed`; `test_toggle_watch_account`.
  - `test_taslaklar_and_paylasilanlar_filter_by_status`.
  - `test_settings_override_budget`; `test_invalid_budget_rejected` (negatif → 400 Türkçe hata); `test_shares_must_sum_to_one`.
  - `test_add_ban_keyword_flags_existing_topic_next_cycle` (Task 9 `run_analysis_and_score_cycle` çağrılarak).
  - `test_events_stream_relays_notify` (`pg_notify` → akışta `event: breaking`, 5 sn zaman aşımı).
- [ ] **Step 2:** FAIL. **Step 3:** Uygula. **Step 4:** PASS.
- [ ] **Step 5:** `git commit -m "Add live updates, browser alerts and admin pages"`

---

### Task 12: Dağıtım ve uçtan uca doğrulama

**Files:** Create `Dockerfile`, `docker-compose.yml`, `README.md`; Modify `.env.example`

- `docker compose up`: `db` (postgres:16), `web` (`alembic upgrade head && uvicorn gundem.web.app:app --host 0.0.0.0 --port 8000`), `worker` (`python -m gundem.worker`, `replicas: 1`).
- README (Türkçe): kurulum; her ortam değişkeni; X maliyet ayarının ilk faturayla doğrulanması; bütçe paylarının anlamı; `seeds/voice_examples.yaml`'ın editör onaylı en iyi 10–20 paylaşımla doldurulması; rakiplerin panelden eklenmesi; yayın yasağı listesinin kim tarafından nasıl güncelleneceği; tek worker kuralı; telif notu; Aşama 2 listesi.

- [ ] **Step 1:** `pytest -q` → tümü PASS.
- [ ] **Step 2:** Yerel Postgres, anahtarsız `python -m gundem.worker` 3 dk → RSS'ten Item ve konu oluşur (`SELECT count(*) FROM topic` > 0), hatalı kaynaklar loglanır.
- [ ] **Step 3:** X anahtarı ortamdaysa: X breaking döngüsü bir kez çalışır, `x_spend` satırı ve `XCursor` güncellenir; harcanan okuma sayısı loglanır. Anahtar yoksa adım atlanır ve raporda belirtilir.
- [ ] **Step 4:** `uvicorn` + Playwright: giriş, Gündem ekran görüntüsü, `J` ile konu değişimi, bir konuda `C` sonrası `interaction` satırı.
- [ ] **Step 5:** `docker compose config` hatasız.
- [ ] **Step 6:** `git commit -m "Add Docker deployment and setup guide"`

---

### Task 13 (Pusholder onaylarsa): Telegram uyarı botu

**Files:** Create `gundem/telegram.py`, `tests/test_telegram.py`; Modify `gundem/worker.py`

- `TelegramNotifier(token, chat_id, http, panel_url)`; `__call__(topic, draft)` — son dakika ve `score >= 85` konular için `POST https://api.telegram.org/bot<token>/sendMessage`: başlık, skor, en iyi X varyantı, panel linki `panel_url + "/?t=" + id`. Aynı konu bir kez gönderilir. `TELEGRAM_BOT_TOKEN` yoksa `Deps.notifier=None`.
- Testler: `test_sends_once_per_topic`, `test_below_85_not_sent`, `test_breaking_always_sent`.
- `git commit -m "Add Telegram alerts for breaking and hot topics"`

---

## Aşama 2'ye kalanlar

Performans sayfası ve paylaşım sonrası etkileşim takibi (X + Windsor IG), skor ağırlıklarının `Interaction` verisiyle ayarlanması, düzeltilmiş taslakların üslup havuzuna önerilmesi, paylaşım saati önerisi, editör tablosu ("rakipten önce girdik"), rakip Instagram (Meta onayı), Ekşi Sözlük (kullanım şartları), YouTube trendleri (API anahtarı), diller arası konu birleştirme.
