# Pusholder Gündem Paneli — Aşama 1 Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Son dakika ve viral gündemi toplayan, puanlayan, Pusholder dilinde X/Instagram taslağı yazan ve bunları beyaz bir editör panelinde sunan servisi kurmak.

**Architecture:** İki süreç, tek Postgres veritabanı. `worker` süreci APScheduler ile kaynakları tarar, haberleri konulara birleştirir, puanlar ve Claude API ile taslak yazar. `web` süreci FastAPI + Jinja2 + HTMX ile paneli sunar, yeni konuları SSE ile canlı iter. Dış servisler (RSS, X API, Claude) ince istemci sınıflarının arkasındadır; testler sahte istemcilerle çalışır.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, HTMX 1.9.12 (CDN), SQLAlchemy 2.x, Alembic, Postgres 16, APScheduler 3.x, httpx, feedparser, rapidfuzz, anthropic SDK, pydantic-settings, pytest, respx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-25-pusholder-gundem-paneli-design.md` (Aşama 1 = bölüm 10.5). Görsel referans: `docs/panel-mockup.html`.

## Global Constraints

- **Otomatik paylaşım yok.** Kodda X veya Instagram'a yazan (POST tweet, publish media) hiçbir çağrı olmaz.
- Çıktı platformları yalnızca X ve Instagram. Taslak: X ≤ 280 karakter; Instagram = açıklama + hashtag listesi.
- Her taslak en az bir kaynağa (`Item.url`) bağlıdır; kaynak metni kopyalanmaz, prompt "yeniden yaz, kaynakta olmayan iddia ekleme" der.
- Tek kaynaklı konu: taslak `verified=False`; panelde "Doğrulanmadı — tek kaynak" etiketi, "kaynağı kontrol ettim" işaretlenene kadar Kopyala pasif. İkinci farklı kaynak gelince `verified=True`.
- X API: yalnızca okuma; günlük bütçe tavanı `X_DAILY_BUDGET_USD` (varsayılan `5.0`), okuma başı maliyet `X_COST_PER_READ_USD` (varsayılan `0.005` — Pusholder'ın faturasıyla doğrulanmalı). Tavan dolunca X çağrısı yapılmaz, istisna fırlatılmaz.
- X'i API dışından kazıma yok.
- Güvenilir katman (tier 1): AA, DHA, İHA, Reuters, AP, AFP, BBC ve resmi X hesapları. Tier 2: büyük haber siteleri. Tier 3: gündem sinyalleri (Google Trends, Reddit).
- Kırmızı son dakika uyarısı yalnızca tier 1 + son dakika işareti + yayın zamanı son 30 dakika içinde olan haberde.
- Panel: tek ortak giriş (`PANEL_USER`, `PANEL_PASSWORD`), beyaz tek tema, Türkçe arayüz metinleri.
- Claude model adı koda yazılmaz; `ANTHROPIC_MODEL` ortam değişkeninden okunur (zorunlu).
- Sırlar (API anahtarları, şifreler) yalnızca ortam değişkeninde; repoya girmez. `.env` `.gitignore`'da.

## Review Focus

1. **Bir RSS kaynağı çökük/bozuk/yavaş** → o kaynak loglanır ve atlanır, diğerleri taranmaya devam eder; zaman aşımı 10 sn. (Test: Task 2 `test_collect_all_skips_failing_source`)
2. **İlk çalıştırmada eski haberler** → akıştaki eski kayıtlar son dakika alarmı üretmez (30 dk kuralı). (Test: Task 2 `test_old_item_is_not_breaking`)
3. **X bütçesi gün ortasında biter** → X işleri sessizce durur, panel bütçe dolu gösterir, RSS hattı çalışır; gece yarısı (Europe/Istanbul) sıfırlanır. (Test: Task 4 `test_budget_exhausted_blocks_and_resets_next_day`)
4. **Claude API hata/zaman aşımı veya 280'i aşan X metni** → konu taslaksız kalır ve `draft_error` dolar; panel "Taslak üretilemedi — Yeniden yaz"; uzun metin bir kez yeniden istenir, yine uzunsa kelime sınırında kesilip `x_truncated=True`. (Test: Task 6 `test_api_error_sets_draft_error`, `test_long_x_text_retried_then_truncated`)
5. **İki editör aynı konuyu aynı anda sahiplenir** → ilk kazanır, ikinciye "Bu konu <ad> tarafından alındı" döner. (Test: Task 9 `test_second_claim_is_rejected`)

---

## Dosya yapısı

```
pusholder-gundem/
  pyproject.toml  docker-compose.yml  Dockerfile  .env.example  .gitignore  README.md
  alembic.ini  migrations/
  gundem/
    config.py        Settings (pydantic-settings)
    db.py            engine, SessionLocal, Base, session_scope()
    models.py        Source, Item, Topic, Draft, WatchAccount, RivalPost, XSpend, Claim
    seeds.py         seeds/sources.yaml + watchlist.yaml → DB (idempotent)
    sources/rss.py   RSS/Atom toplayıcı
    sources/x.py     XClient (okuma + bütçe)
    budget.py        XBudget
    breaking.py      is_breaking()
    topics.py        normalize_title(), assign_topic()
    scoring.py       score_topic()
    voice.py         Pusholder üslup örnekleri
    drafting.py      DraftWriter (Claude)
    rivals.py        rakip kapsama + istatistik
    pipeline.py      run_breaking_cycle(), run_gundem_cycle(), run_x_cycle(), run_draft_cycle()
    worker.py        APScheduler giriş noktası
    web/app.py       FastAPI uygulaması, auth, rotalar
    web/events.py    SSE yayıncısı (Postgres LISTEN/NOTIFY)
    web/templates/   base.html, gundem.html, _topic_row.html, _topic_detail.html, rakipler.html, kaynaklar.html, izleme.html, ayarlar.html, login.html
    web/static/      panel.css, panel.js, alert.mp3 (kısa bip, data üretilebilir)
  seeds/sources.yaml  seeds/watchlist.yaml  seeds/voice_examples.yaml
  tests/  conftest.py + test_*.py
```

---

### Task 1: İskelet, ayarlar, veritabanı modelleri

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `gundem/__init__.py`, `gundem/config.py`, `gundem/db.py`, `gundem/models.py`, `alembic.ini`, `migrations/` (ilk revizyon), `tests/conftest.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Settings` alanları: `database_url: str`, `anthropic_api_key: str | None`, `anthropic_model: str | None`, `x_bearer_token: str | None`, `x_daily_budget_usd: float = 5.0`, `x_cost_per_read_usd: float = 0.005`, `draft_score_threshold: int = 60`, `hot_score: int = 70`, `breaking_max_age_min: int = 30`, `topic_window_hours: int = 6`, `topic_similarity: int = 80`, `panel_user: str`, `panel_password: str`, `session_secret: str`, `timezone: str = "Europe/Istanbul"`. `get_settings() -> Settings` (lru_cache).
  - `session_scope()` context manager → `Session`.
  - Modeller (tümünde `id` int PK, `created_at` timestamptz):
    - `Source(name, kind: Literal["rss","x_account","trends"], url, tier: int 1|2|3, lang: "tr"|"en", is_breaking_feed: bool, enabled: bool, last_ok_at, last_error)`
    - `Item(source_id FK, external_id, title, summary, url, published_at, lang, is_breaking: bool, topic_id FK nullable)`; unique `(source_id, external_id)`.
    - `Topic(title, lang, first_seen_at, last_seen_at, is_breaking: bool, score: int=0, x_velocity: float=0, source_count: int=0, verified: bool=False, rival_covered_by: str|None, rival_covered_at, status: Literal["new","claimed","shared","skipped"]="new", claimed_by: str|None, claimed_at, draft_error: str|None)`
    - `Draft(topic_id FK, x_text, ig_caption, ig_hashtags: list[str] (JSON), x_truncated: bool, created_at)` — konu başına birden çok (Yeniden yaz); en yenisi geçerli.
    - `WatchAccount(handle, x_user_id, role: Literal["breaking","journalist","rival"], tier: int, last_tweet_id, enabled)`
    - `RivalPost(watch_account_id FK, tweet_id unique, text, created_at_x, like_count, repost_count, reply_count, topic_id FK nullable)`
    - `XSpend(day: date, reads: int, usd: float)`; unique `day`.

- [ ] **Step 1: Test yaz** — `tests/conftest.py`: test veritabanı `TEST_DATABASE_URL` (yoksa `postgresql+psycopg://postgres@localhost/gundem_test`), her testte tablolar oluşturulur/silinir, `db` fixture'ı `Session` verir. `tests/test_models.py`:
  - `test_settings_defaults`: `x_daily_budget_usd == 5.0`, `draft_score_threshold == 60`, `breaking_max_age_min == 30`.
  - `test_item_unique_per_source`: aynı `(source_id, external_id)` ikinci ekleme `IntegrityError`.
  - `test_topic_defaults`: yeni `Topic` → `status == "new"`, `verified is False`, `score == 0`.
- [ ] **Step 2: Çalıştır, başarısız olduğunu gör** — `pytest tests/test_models.py -v` → FAIL (modül yok).
- [ ] **Step 3: Uygula** — `config.py`, `db.py`, `models.py`; Alembic ilk revizyonu `alembic revision --autogenerate -m "initial"`.
- [ ] **Step 4: Çalıştır** — `pytest tests/test_models.py -v` → PASS; `alembic upgrade head` boş DB'de hatasız.
- [ ] **Step 5: Commit** — `git commit -m "Add project skeleton, settings and database models"`

---

### Task 2: RSS toplayıcı, kaynak tohumları, son dakika tespiti

**Files:**
- Create: `gundem/sources/__init__.py`, `gundem/sources/rss.py`, `gundem/breaking.py`, `gundem/seeds.py`, `seeds/sources.yaml`, `tests/fixtures/aa_sondakika.xml`, `tests/fixtures/bbc_world.xml`, `tests/test_rss.py`, `tests/test_breaking.py`

**Interfaces:**
- Consumes: `Source`, `Item`, `session_scope`, `get_settings`
- Produces:
  - `RawItem` (dataclass): `external_id: str, title: str, summary: str, url: str, published_at: datetime (UTC), lang: str`
  - `fetch_rss(source: Source, client: httpx.Client) -> list[RawItem]` — zaman aşımı 10 sn; `external_id` = entry `id` yoksa `link`.
  - `is_breaking(raw: RawItem, source: Source, now: datetime) -> bool`
  - `collect(sources: list[Source], session: Session, client: httpx.Client, now: datetime) -> list[Item]` — yalnızca yeni Item'ları ekler ve döndürür; hata veren kaynakta `last_error` yazar, devam eder; başarılı kaynakta `last_ok_at=now`.
  - `load_seeds(session: Session) -> None` — `seeds/*.yaml`'ı ada göre upsert eder.

`is_breaking` kuralı: `source.tier == 1` VE `now - published_at <= breaking_max_age_min` VE (`source.is_breaking_feed` VEYA başlık büyük/küçük harf duyarsız `"son dakika"`, `"flaş"`, `"breaking"`, `"urgent"` içerir).

`seeds/sources.yaml` en az: AA, DHA, İHA (tier 1, tr, son dakika akışı varsa `is_breaking_feed: true`), BBC World + BBC Türkçe (tier 1), Reuters/AP/AFP için açık akışı olanlar (yoksa `enabled: false` ve yorumda "abonelik gerekir"), 4–6 büyük Türk haber sitesi (tier 2), Google Trends TR (`kind: trends`, URL `https://trends.google.com/trending/rss?geo=TR`, tier 3), Reddit `r/Turkey` RSS (tier 3). Uygulayıcı her URL'yi bir kez `curl` ile doğrular; çalışmayanı `enabled: false` yapar ve `last_error` yorumu bırakır.

- [ ] **Step 1: Test yaz**
  - `test_fetch_rss_parses_items`: `respx` ile `aa_sondakika.xml` → en az 1 `RawItem`, `title`, `url`, `published_at.tzinfo is not None`.
  - `test_collect_dedupes`: aynı akış iki kez `collect` → ikinci çağrı `[]` döner.
  - `test_collect_all_skips_failing_source`: biri 500, biri geçerli iki kaynak → geçerlinin Item'ları eklenir, hatalı kaynağın `last_error` dolu.
  - `test_breaking_tier1_fresh_flagged`: tier 1, 5 dk önce, başlık "SON DAKİKA: ..." → `True`.
  - `test_old_item_is_not_breaking`: aynı ama 45 dk önce → `False`.
  - `test_tier2_never_breaking`: tier 2 + "son dakika" → `False`.
  - `test_load_seeds_idempotent`: iki kez `load_seeds` → `Source` sayısı değişmez.
- [ ] **Step 2: Çalıştır** — `pytest tests/test_rss.py tests/test_breaking.py -v` → FAIL.
- [ ] **Step 3: Uygula** — feedparser ile ayrıştır; `published_parsed` yoksa `now` kullan.
- [ ] **Step 4: Çalıştır** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add RSS collector, source seeds and breaking-news detection"`

---

### Task 3: Konu birleştirme

**Files:**
- Create: `gundem/topics.py`, `tests/test_topics.py`

**Interfaces:**
- Consumes: `Item`, `Topic`, `Source`
- Produces:
  - `normalize_title(title: str) -> str` — küçük harf (Türkçe: `İ→i`, `I→ı`), "son dakika", "flaş", "breaking" önekleri ve noktalama atılır, boşluk sadeleşir.
  - `assign_topic(item: Item, session: Session, now: datetime) -> Topic` — son `topic_window_hours` içindeki, aynı dildeki konularla `rapidfuzz.fuzz.token_set_ratio(normalize(item.title), normalize(topic.title)) >= topic_similarity` olan en yüksek eşleşmeye bağlar; yoksa yeni konu açar. Bağladıktan sonra `topic.source_count` = farklı `source_id` sayısı; `source_count >= 2` → `verified=True`; `item.is_breaking` → `topic.is_breaking=True`; `last_seen_at=now`.

Diller arası birleştirme (EN↔TR) Aşama 1'de yok; global haber ayrı konu olarak görünür.

- [ ] **Step 1: Test yaz**
  - `test_normalize_strips_prefix_and_turkish_case`: `"SON DAKİKA: İstanbul'da Deprem!"` → `"istanbulda deprem"`.
  - `test_similar_titles_merge`: "İstanbul'da 4.2 büyüklüğünde deprem" ve "SON DAKİKA İstanbul'da 4,2 büyüklüğünde deprem" (farklı kaynaklar) → aynı `Topic`, `source_count == 2`, `verified is True`.
  - `test_same_source_twice_stays_unverified`: aynı kaynaktan iki benzer başlık → `source_count == 1`, `verified is False`.
  - `test_outside_window_opens_new_topic`: 7 saat önceki konu → yeni konu.
  - `test_different_language_not_merged`.
- [ ] **Step 2: Çalıştır** — `pytest tests/test_topics.py -v` → FAIL.
- [ ] **Step 3: Uygula.**
- [ ] **Step 4: Çalıştır** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Group items from different sources into topics"`

---

### Task 4: X istemcisi ve bütçe koruması

**Files:**
- Create: `gundem/budget.py`, `gundem/sources/x.py`, `gundem/seeds.py` (modify: watchlist), `seeds/watchlist.yaml`, `tests/test_budget.py`, `tests/test_x.py`

**Interfaces:**
- Consumes: `XSpend`, `WatchAccount`, `get_settings`
- Produces:
  - `XBudget(session: Session, settings: Settings, today: date)`:
    - `remaining_reads() -> int`
    - `try_reserve(reads: int) -> bool` — kalan yetmiyorsa `False`, yetiyorsa `XSpend` satırını artırır ve `True`.
    - `spent_usd() -> float`, `cap_usd() -> float`
    - `poll_interval_seconds(base: int = 120) -> int` — kalan bütçe oranı `r`: `r > 0.5 → base`, `0.2 < r ≤ 0.5 → base*2`, `0 < r ≤ 0.2 → base*5`, `r == 0 → 0` (dur).
  - `XTweet` (dataclass): `id, author_id, text, created_at, like_count, repost_count, reply_count, quote_count`
  - `XClient(bearer: str, budget: XBudget, http: httpx.Client)`:
    - `user_tweets(user_id: str, since_id: str | None, max_results: int = 10) -> list[XTweet]` — `GET /2/users/:id/tweets`, `tweet.fields=created_at,public_metrics`, `exclude=replies`.
    - `search_recent(query: str, max_results: int = 25) -> list[XTweet]` — `GET /2/tweets/search/recent`, `-is:retweet lang:tr OR lang:en`.
    - İstek öncesi `budget.try_reserve(max_results)`; `False` ise X'e gitmeden `[]` döner. Yanıtta dönen tweet sayısı rezervden azsa fark iade edilmez (basitlik; maliyet üst sınırı korunur).
    - 429 → `[]` ve log; istisna fırlatmaz.
  - `velocity(tweets: list[XTweet], now: datetime) -> float` — dakika başına toplam etkileşim (like+repost+reply+quote) / ilk tweet'ten bu yana geçen dakika (en az 1).

`seeds/watchlist.yaml`: AA, DHA, İHA, Reuters, AP, AFP, BBC Breaking resmi hesapları (`role: breaking, tier: 1`), rakipler için yer tutucu kayıtlar (`role: rival`, `enabled: false`, yorumda "Pusholder doldurmalı"). `x_user_id` boşsa worker ilk çalıştırmada `GET /2/users/by/username/:handle` ile doldurur (bütçeden 1 okuma).

- [ ] **Step 1: Test yaz**
  - `test_reserve_within_budget`: cap $5, maliyet $0.005 → `remaining_reads() == 1000`; `try_reserve(25)` → `True`, kalan 975.
  - `test_budget_exhausted_blocks_and_resets_next_day`: 1000 rezerve → `try_reserve(1)` `False`; ertesi gün `XBudget(..., today+1)` → `True`.
  - `test_poll_interval_scales`: kalan %60 → 120; %30 → 240; %10 → 600; %0 → 0.
  - `test_client_skips_http_when_no_budget`: bütçe 0, `respx` rotası çağrılmamış olmalı, sonuç `[]`.
  - `test_user_tweets_parses_metrics`: örnek JSON → `XTweet.like_count` doğru.
  - `test_rate_limited_returns_empty`: 429 → `[]`.
  - `test_velocity`: 3 tweet, toplam 600 etkileşim, 10 dk → `60.0`.
- [ ] **Step 2: Çalıştır** — `pytest tests/test_budget.py tests/test_x.py -v` → FAIL.
- [ ] **Step 3: Uygula** — `XBudget.try_reserve` satırı `SELECT ... FOR UPDATE` ile kilitler (iki işin aynı anda harcamasını önler).
- [ ] **Step 4: Çalıştır** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add read-only X client with daily budget guard"`

---

### Task 5: Puanlama

**Files:**
- Create: `gundem/scoring.py`, `tests/test_scoring.py`

**Interfaces:**
- Consumes: `Topic`, `Item`, `Source`
- Produces: `score_topic(topic: Topic, source_tiers: list[int], now: datetime) -> int`

Başlangıç formülü (ağırlıklar modül sabitleri, Aşama 2'de veriyle ayarlanacak):

```
velocity_n = min(1, topic.x_velocity / 300)
source_n   = min(1, sum({1:1.0, 2:0.6, 3:0.3}[t] for t in tiers) / 3)
no_rival   = 1.0 if topic.rival_covered_by is None else 0.3
recency    = max(0, 1 - age_minutes(topic.first_seen_at) / 180)
score = floor(100 * (0.40*velocity_n + 0.25*source_n + 0.20*no_rival + 0.15*recency) + 0.5)   # yarım yukarı yuvarlama
breaking konu: score = max(score, 90)
```

- [ ] **Step 1: Test yaz**
  - `test_fresh_fast_uncovered_is_hot`: velocity 300, tiers [1,1,2], rakip yok, 0 dk → `97`.
  - `test_rival_lowers_score`: aynı, rakip girmiş → `83`.
  - `test_old_slow_single_tier3`: velocity 0, [3], rakip yok, 200 dk → `23` (22.5 yukarı yuvarlanır).
  - `test_breaking_floor_90`.
- [ ] **Step 2: Çalıştır** — FAIL. **Step 3: Uygula.** **Step 4: Çalıştır** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add initial viral score formula"`

---

### Task 6: Pusholder dilinde taslak yazımı (Claude)

**Files:**
- Create: `gundem/voice.py`, `gundem/drafting.py`, `seeds/voice_examples.yaml`, `tests/test_drafting.py`

**Interfaces:**
- Consumes: `Topic`, `Item`, `Draft`, `get_settings`
- Produces:
  - `load_voice_examples() -> list[dict]` — `seeds/voice_examples.yaml` (`platform: x|instagram`, `text`, `engagement`). Başlangıçta 6–10 örnek yer tutucu; Pusholder kendi en iyi paylaşımlarıyla değiştirecek (README'de anlatılır).
  - `build_messages(topic: Topic, items: list[Item], examples: list[dict]) -> tuple[str, list[dict]]` — (system, messages).
  - `DraftWriter(client: anthropic.Anthropic, model: str)`
    - `write(topic: Topic, items: list[Item], session: Session) -> Draft | None` — başarıda `Draft` ekler ve `topic.draft_error=None`; hata/zaman aşımında (`timeout=30`) `topic.draft_error` = kısa Türkçe mesaj, `None` döner.

System prompt şunları sabitler (Türkçe): Pusholder'ın genç, hızlı, merak uyandıran ama abartmayan dili; örnekler ilham içindir, kopyalanmaz; **yalnızca verilen kaynaklardaki bilgileri kullan, ekleme yapma**; kaynak metni birebir kopyalama; global haberse Türkçeye çevir ve Türkiye'deki okur için neden önemli olduğunu bir cümleyle ekle; kesin olmayan bilgi için "iddia edildi / bildirildi" kullan. Çıktı yalnızca JSON: `{"x": str, "instagram": {"caption": str, "hashtags": [str]}}`.

Kurallar: JSON ayrıştırılamazsa bir kez yeniden dene, yine olmazsa `draft_error`. `len(x) > 280` → bir kez "280 karakteri aşma" ek mesajıyla yeniden iste; yine uzunsa son boşlukta keserek ≤ 279 + "…" yap, `x_truncated=True`. Hashtag'ler `#` ile başlar, en fazla 8.

- [ ] **Step 1: Test yaz** (sahte `anthropic` istemcisi: `messages.create` sırayla verilen yanıtları döndürür)
  - `test_build_messages_includes_sources_and_rule`: system içinde "ekleme yapma" geçer; user mesajında her `item.url` ve `item.title` geçer.
  - `test_write_creates_draft`: geçerli JSON → `Draft.x_text`, `ig_hashtags` liste, `topic.draft_error is None`.
  - `test_long_x_text_retried_then_truncated`: iki kez 300 karakter → `len(x_text) <= 280`, `x_truncated is True`, `create` 2 kez çağrıldı.
  - `test_invalid_json_retried_once`: önce bozuk sonra geçerli → `Draft` oluşur.
  - `test_api_error_sets_draft_error`: `anthropic.APITimeoutError` → `None`, `topic.draft_error` dolu.
- [ ] **Step 2: Çalıştır** — FAIL. **Step 3: Uygula.** **Step 4: Çalıştır** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "Write X and Instagram drafts in Pusholder voice with Claude"`

---

### Task 7: Rakip takibi

**Files:**
- Create: `gundem/rivals.py`, `tests/test_rivals.py`

**Interfaces:**
- Consumes: `XClient.user_tweets`, `WatchAccount`, `RivalPost`, `Topic`, `normalize_title`
- Produces:
  - `ingest_rival_tweets(account: WatchAccount, tweets: list[XTweet], session: Session) -> list[RivalPost]` — yeni olanları ekler, `account.last_tweet_id` günceller.
  - `match_rival_coverage(session: Session, now: datetime) -> int` — son `topic_window_hours` içindeki eşleşmemiş `RivalPost`'ları konulara `token_set_ratio >= topic_similarity - 10` ile bağlar; konuda `rival_covered_by` boşsa hesabın handle'ı ve zamanı yazılır. Bağlanan sayısını döndürür.
  - `rival_stats(session: Session, since: datetime) -> list[dict]` — her rakip + Pusholder için `{"handle", "posts", "avg_engagement", "best_text", "last_post_at"}`. Pusholder kendi hesabı `WatchAccount(role="own")` olarak eklenir (rol Literal'ine `"own"` eklenir).

- [ ] **Step 1: Test yaz**
  - `test_ingest_skips_known_tweets`.
  - `test_rival_tweet_marks_topic_covered`: konu "Final maçı biletleri satışa çıktı", rakip tweet "Final maçının biletleri satışta! Fiyatlar..." → `rival_covered_by == "rakip_a"`.
  - `test_first_rival_wins`: iki rakip → ilk zaman damgalı kalır.
  - `test_rival_stats_avg`: 2 tweet (100 ve 300 etkileşim) → `avg_engagement == 200`.
- [ ] **Step 2–4:** FAIL → uygula → PASS (`pytest tests/test_rivals.py -v`).
- [ ] **Step 5: Commit** — `git commit -m "Track rival X accounts and mark topics they covered"`

---

### Task 8: İş hattı ve zamanlayıcı

**Files:**
- Create: `gundem/pipeline.py`, `gundem/worker.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 2–7'nin tüm `Produces` öğeleri
- Produces:
  - `run_breaking_cycle(deps: Deps, now) -> list[Topic]` — tier 1 kaynakları toplar, `assign_topic`, puanlar; yeni son dakika konularını döndürür ve `pg_notify('gundem', json{"type":"breaking","topic_id"})`.
  - `run_gundem_cycle(deps, now) -> int` — tier 2–3 kaynaklar; yeni/güncellenen konu sayısı; her güncellemede `pg_notify('gundem', {"type":"topic","topic_id"})`.
  - `run_x_cycle(deps, now) -> None` — izleme listesi (`user_tweets`), rakip eşleme; ardından skoru ≥ 40 olup `x_velocity` son 15 dk'da ölçülmemiş en fazla 5 aday konu için `search_recent(normalize_title(...) ilk 6 kelime)` → `x_velocity`; yeniden puanlar.
  - `run_draft_cycle(deps, now) -> int` — `is_breaking` VEYA `score >= draft_score_threshold` olup taslağı olmayan ve `draft_error` boş konular için `DraftWriter.write`; ardından `pg_notify(..."draft")`.
  - `Deps` (dataclass): `session_factory, http: httpx.Client, x: XClient | None, writer: DraftWriter | None, settings`. `x`/`writer` `None` ise ilgili adımlar atlanır (anahtar yoksa servis yine RSS ile çalışır).
  - `worker.main()` — `load_seeds`, ardından APScheduler: breaking 90 sn, gündem 300 sn, draft 60 sn, x döngüsü her çalışmadan sonra `XBudget.poll_interval_seconds()` ile kendini yeniden planlar (0 ise ertesi gün 00:01 Europe/Istanbul). Her iş `max_instances=1`, istisnalar loglanır, zamanlayıcı ölmez.

- [ ] **Step 1: Test yaz** (respx RSS + sahte XClient + sahte DraftWriter)
  - `test_breaking_cycle_creates_breaking_topic_and_notifies`.
  - `test_draft_cycle_only_above_threshold_or_breaking`: skor 50 konu taslaksız kalır, skor 65 ve breaking konular taslak alır.
  - `test_cycle_runs_without_x_and_claude_keys`: `x=None, writer=None` → hata yok, konular oluşur.
  - `test_x_cycle_limits_candidates_to_5`.
- [ ] **Step 2–4:** FAIL → uygula → PASS (`pytest tests/test_pipeline.py -v`).
- [ ] **Step 5: Commit** — `git commit -m "Wire collection, scoring and drafting into scheduled jobs"`

---

### Task 9: Panel — giriş, Gündem sayfası, konu işlemleri

**Files:**
- Create: `gundem/web/__init__.py`, `gundem/web/app.py`, `gundem/web/templates/{base,login,gundem,_topic_row,_topic_detail}.html`, `gundem/web/static/panel.css`, `tests/test_web.py`

**Interfaces:**
- Consumes: modeller, `rival_stats`, `XBudget`, `DraftWriter` (Yeniden yaz için)
- Produces (rotalar; tümü `/login` hariç oturum ister):
  - `GET /login`, `POST /login` (form: `user`, `password`, `editor_name`) → imzalı çerez (`itsdangerous`, `session_secret`); `editor_name` sahiplenmede görünen ad.
  - `GET /` → Gündem: özet şeridi (sıcak konu = son 1 saatte `score >= hot_score`, bekleyen taslak = `status in (new, claimed)` ve taslaklı, rakipten önce girilen = bugün `shared` olup `rival_covered_at` boş veya paylaşımdan sonra, X bütçesi `spent/cap`), son dakika şeridi, sol liste (son 24 saat, `status != skipped`, `is_breaking desc, score desc`), filtreler `?f=all|hot|norival`, ilk konunun detayı.
  - `GET /topics/{id}` → `_topic_detail.html` parçası (HTMX).
  - `POST /topics/{id}/claim` → başarı 200; başkası almışsa 409 + "Bu konu {ad} tarafından alındı". Koşullu güncelleme: `UPDATE topic SET status='claimed', claimed_by=:me WHERE id=:id AND (claimed_by IS NULL OR claimed_by=:me)`.
  - `POST /topics/{id}/verify` → panelde "kaynağı kontrol ettim" (konu `verified=True`).
  - `POST /topics/{id}/shared` → `status="shared"`.
  - `POST /topics/{id}/skip` → `status="skipped"`.
  - `POST /topics/{id}/redraft` → `DraftWriter.write`; yeni taslakla detay parçası.
  - `GET /health` → `{"db": "ok"}` (oturumsuz).

Görsel: `docs/panel-mockup.html` v2'nin CSS'i `panel.css`'e taşınır; yerleşim, metinler ve sınıf adları oradan. Kopyala butonu `navigator.clipboard.writeText`, başarısızsa metni seçer. Tek kaynaklı konuda Kopyala `disabled` ve yanında "Doğrulanmadı — tek kaynak" etiketi + "Kaynağı kontrol ettim" butonu. `draft_error` varsa taslak yerinde "Taslak üretilemedi — Yeniden yaz". X metninin altında `n / 280` sayacı.

- [ ] **Step 1: Test yaz** (FastAPI `TestClient`)
  - `test_requires_login`: `/` → 303 `/login`.
  - `test_login_wrong_password_rejected`.
  - `test_gundem_lists_breaking_first`.
  - `test_second_claim_is_rejected`: editör A claim 200, editör B claim 409 ve gövdede "A".
  - `test_unverified_copy_disabled`: tek kaynaklı konu detayında `disabled` ve "Doğrulanmadı" var; `/verify` sonrası yok.
  - `test_skip_hides_topic`.
  - `test_draft_error_shows_retry`.
- [ ] **Step 2–4:** FAIL → uygula → PASS (`pytest tests/test_web.py -v`).
- [ ] **Step 5: Commit** — `git commit -m "Add editor panel with login, topic list and topic actions"`

---

### Task 10: Canlı güncelleme, son dakika uyarısı, yönetim sayfaları

**Files:**
- Create: `gundem/web/events.py`, `gundem/web/static/panel.js`, `gundem/web/static/alert.mp3`, `gundem/web/templates/{rakipler,kaynaklar,izleme,ayarlar}.html`, `tests/test_admin_pages.py`
- Modify: `gundem/web/app.py`, `gundem/config.py` (ayarların DB'den okunması)

**Interfaces:**
- Consumes: `pg_notify('gundem', ...)` (Task 8), `rival_stats`
- Produces:
  - `GET /events` → SSE; Postgres `LISTEN gundem` mesajlarını `event: <type>\ndata: <json>` olarak iletir; 25 sn'de bir `: ping`.
  - `panel.js`: `EventSource('/events')`; `topic`/`draft` → sol listeyi `htmx.ajax('GET', '/?partial=list')` ile yeniler; `breaking` → kırmızı şerit + `alert.mp3` (ses ilk kullanıcı tıklamasından sonra açılır; üstte "Sesli uyarıyı aç" butonu).
  - `GET /taslaklar` → taslağı olan ve `status in (new, claimed)` konular, en yeni önce; `GET /paylasilanlar` → `status == shared`, en yeni önce. İkisi de `_topic_row.html` kullanır.
  - `GET /rakipler` → `rival_stats(son 1 saat)` ve `(bugün)` tabloları.
  - `GET/POST /kaynaklar` → kaynak listesi; ekle (ad, url, tier, dil, son dakika akışı mı), aç/kapat; `last_ok_at`/`last_error` görünür.
  - `GET/POST /izleme` → izleme listesi; handle ekle (rol, tier), aç/kapat.
  - `GET/POST /ayarlar` → `x_daily_budget_usd`, `x_cost_per_read_usd`, `draft_score_threshold`, `hot_score`. Değerler `AppSetting(key, value)` tablosunda (Task 1 modeline eklenir, Alembic revizyonu); `get_effective_settings(session) -> Settings` ortam değerini DB değeriyle ezer; worker her döngüde bunu kullanır.

- [ ] **Step 1: Test yaz**
  - `test_add_source_then_listed`.
  - `test_taslaklar_and_paylasilanlar_filter_by_status`.
  - `test_toggle_watch_account`.
  - `test_settings_override_budget`: `/ayarlar` POST `x_daily_budget_usd=2` → `get_effective_settings(...).x_daily_budget_usd == 2.0`.
  - `test_invalid_budget_rejected`: negatif değer → 400 ve Türkçe hata.
  - `test_events_stream_relays_notify`: `pg_notify` sonrası SSE akışında `event: breaking` satırı (zaman aşımı 5 sn).
- [ ] **Step 2–4:** FAIL → uygula → PASS (`pytest tests/test_admin_pages.py -v`).
- [ ] **Step 5: Commit** — `git commit -m "Add live updates, breaking alert and admin pages"`

---

### Task 11: Dağıtım ve uçtan uca doğrulama

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`
- Modify: `.env.example`

**Interfaces:**
- Produces: `docker compose up` ile `db` (postgres:16), `web` (uvicorn `gundem.web.app:app` :8000), `worker` (`python -m gundem.worker`); `web` başlarken `alembic upgrade head`.

README (Türkçe): kurulum, `.env` değişkenleri (her biri ne işe yarar), X maliyet ayarının faturayla doğrulanması, `seeds/voice_examples.yaml`'ın Pusholder'ın en iyi 10–20 paylaşımıyla doldurulması, rakip hesaplarının `seeds/watchlist.yaml`'a veya panelden eklenmesi, Aşama 2'de neler geleceği. Telif notu: taslaklar kaynağı yeniden yazar, kaynak belirtilmeli.

- [ ] **Step 1:** `pytest -q` → tüm testler PASS.
- [ ] **Step 2:** Yerel Postgres ile `alembic upgrade head && python -m gundem.worker` 3 dakika çalıştır (Claude/X anahtarı olmadan) → loglarda en az bir RSS kaynağından Item eklendiği ve hata veren kaynakların atlandığı görülür; `SELECT count(*) FROM topic` > 0.
- [ ] **Step 3:** `uvicorn gundem.web.app:app` → Playwright ile giriş yap, Gündem sayfasının ekran görüntüsü; liste dolu, detay açılıyor, sağ üstte bütçe görünüyor.
- [ ] **Step 4:** `docker compose config` hatasız.
- [ ] **Step 5: Commit** — `git commit -m "Add Docker deployment and setup guide"`

---

## Kapsam notu (spec'ten bilinçli sapmalar, Aşama 2'ye)

- **Ekşi Sözlük gündemi:** Açık API/RSS yok; HTML kazıma kullanım şartları açısından riskli. Aşama 1'de yok; Pusholder onaylarsa eklenti olarak eklenir.
- **YouTube trendleri:** YouTube Data API anahtarı gerektirir; Aşama 1'de yok.
- **Diller arası konu birleştirme** (EN↔TR): yok.
