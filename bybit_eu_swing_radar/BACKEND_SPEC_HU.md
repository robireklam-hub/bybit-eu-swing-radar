# BYBIT EU SWING RADAR — BACKEND SPECIFIKÁCIÓ

## 1. Cél
Folyamatosan gyűjtött Bybit EU és Coinalyze adatokból:
1. felépíteni a kereskedhető univerzumot;
2. előszűrni a likvid piacokat;
3. technikai és derivatív feature-öket számítani;
4. long/short jelölteket rangsorolni;
5. állapotváltozáskor riasztást létrehozni;
6. a GPT-nek rövid, időbélyegzett, auditálható JSON-t adni.

## 2. Források

### Bybit EU
Base URL: `https://api.bybit.eu`

Nyilvános V5 végpontok:
- `/v5/market/time`
- `/v5/market/instruments-info`
- `/v5/market/tickers`
- `/v5/market/kline`
- `/v5/market/orderbook`
- `/v5/market/recent-trade`
- ahol elérhető: `/v5/market/open-interest`
- `/v5/market/funding/history`
- `/v5/market/account-ratio`

Felhasználói/végrehajtási ellenőrzéshez csak read-only kulcs:
- instrumentum és margin/short elérhetőség;
- saját díjszint és kölcsönözhetőség, ha az EU API ezt engedi.

### Coinalyze
Base URL: `https://api.coinalyze.net/v1`
Auth header: `api_key`
Rate limit: 40 symbol-call/perc. Egy kérés legfeljebb 20 szimbólumot tartalmazhat, de minden szimbólum külön hívásnak számít.

Használt végpontok:
- `/future-markets`
- `/open-interest`
- `/funding-rate`
- `/predicted-funding-rate`
- `/open-interest-history`
- `/funding-rate-history`
- `/predicted-funding-rate-history`
- `/liquidation-history`
- `/long-short-ratio-history`
- `/ohlcv-history`

## 3. Adatgyűjtési ütemezés
- Bybit tickers: 1 perc.
- Bybit 1H kline frissítés: 5 perc; csak lezárt gyertyát használj.
- Bybit 4H kline: óránként és 4H zárás után.
- Bybit 1D kline: óránként és napi zárás után.
- Order book snapshot: top előszűrt 30 coinra 5 perc.
- Coinalyze current OI/funding: top 30 coinra 15 perc, rate-limit queue-val.
- Coinalyze history: top 20 coinra óránként.
- Teljes score: óránként.
- Mély scan: minden lezárt 4H gyertyánál.
- Universe refresh: naponta és listázási változáskor.

## 4. Kétlépcsős szűrés

### Stage A — olcsó Bybit előszűrés
Kizárás:
- nem Trading státusz;
- stablecoin/stablecoin;
- 24h turnover < konfigurált minimum;
- spread > konfigurált maximum;
- < 60 nap adat;
- extrém gap vagy hibás feed;
- túl nagy becsült slippage.

Rangsor:
- turnover;
- ATR/BB kompresszió;
- relatív volumen;
- 1H/4H breakout közelség;
- BTC relatív erő;
- trendstruktúra.

A Stage A maximum 30 jelöltet ad át.

### Stage B — Coinalyze enrichment
A top jelöltekre:
- OI változás 1H/4H/24H;
- funding aktuális, percentilis és trend;
- predicted funding;
- buy/sell flow, ha elérhető;
- liquidation flow;
- long/short ratio;
- adatminőség és coverage.

## 5. Feature-k

### Market structure
- 1D és 4H swing high/low;
- HH/HL, LH/LL;
- BOS/CHOCH;
- range high/low;
- breakout, failed breakout, reclaim;
- távolság kulcsszintektől ATR-ben.

### Volatilitás
- ATR(14) és ATR percentilis 90 napra;
- Bollinger width és percentilis;
- realized volatility 10/20;
- NR4/NR7;
- range compression;
- gap/impulse flag.

### Volume/order flow
- relative volume 20;
- volume z-score;
- taker buy/sell arány, ha elérhető;
- order-book imbalance 0.5%, 1%, 2%;
- spread bps;
- slippage becslés konfigurált pozícióméretre.

### Derivatívák
- OI delta 1H/4H/24H;
- price–OI quadrant;
- funding z-score/percentilis;
- predicted funding változás;
- long/short crowding;
- long és short liquidation flow;
- liquidation spike z-score.

### Relatív erő
- coin/BTC 4H és 1D momentum;
- coin/ETH momentum;
- beta-adjusted excess return;
- BTC emelkedő/eső rezsim alatti viselkedés.

## 6. Pontozás

### Expansion Score 0–100
- ATR/BB/RV kompresszió: 30
- volume gyorsulás: 15
- OI gyorsulás/anomália: 15
- range érettség és breakout közelség: 15
- order-book/spread javulás: 10
- liquidation/crowding feszültség: 10
- adatfrissesség: 5

### Direction Score -100…+100
- 1D/4H market structure: ±25
- relatív erő/gyengeség: ±15
- price–OI kapcsolat: ±15
- breakout/reclaim/sweep: ±15
- buy/sell flow: ±10
- funding/crowding: ±10
- BTC rezsim: ±10

### Quality Score 0–100
- likviditás és slippage: 20
- reális RR: 25
- konfluencia: 25
- trigger tisztasága: 15
- adatminőség: 15

### Setup Score
`0.35 * expansion_score + 0.35 * abs(direction_score) + 0.30 * quality_score`

Penalties:
- már megfutott mozgás: -5…-20;
- extrém spread/slippage: kizárás;
- funding crowding: -5…-15;
- hiányzó Coinalyze coverage: -5…-15;
- nem ellenőrzött shortolhatóság: short kizárás;
- RR < 2: kizárás;
- adat késés: -5…-30.

## 7. Setup állapotgép
- WATCH: score >= 60, trigger még távol.
- ARMED: score >= 70, trigger 1 ATR-en belül.
- TRIGGERED: lezárt 1H vagy 4H gyertya teljesítette a feltételt.
- MANAGED: nyitott setup követése, ha később pozíciókövetés készül.
- INVALIDATED: invalidation szintet lezárt gyertya vagy előírt árérintés megsértette.
- EXPIRED: trigger nélkül lejárt vagy score < 55.

Minden állapotváltást naplózni kell.

## 8. Public API a GPT Action számára
- `GET /health`
- `GET /v1/scan?direction=both&limit=3&min_score=70`
- `GET /v1/market-regime`
- `GET /v1/setup/{symbol}`
- `GET /v1/watchlist?limit=10`
- `GET /v1/alerts?since=...&limit=20`
- `GET /v1/data-status`

Az endpointok gyorsítótárból olvassanak. A GPT kérésére ne induljon teljes piaci scan.

## 9. Biztonság
- Bybit kulcs kizárólag read-only.
- Nincs order-create, order-amend, order-cancel vagy withdrawal endpoint.
- Kulcsok csak szerveroldali secret store-ban.
- GPT Action saját `X-Radar-Key` kulccsal hitelesít.
- Rate limit, request logging, IP/abuse védelem.
- JSON válaszban soha nincs upstream API-kulcs.
- Minden adat UTC-ben tárolódik, válaszban UTC és Europe/Budapest idő is szerepel.

## 10. Backtest és audit
Mentsd:
- scan időpont;
- feature snapshot;
- score-ok;
- trigger/invalidation/target;
- későbbi MFE/MAE;
- TP/SL/expired eredmény;
- BTC-rezsim;
- költség és becsült slippage.

Kötelező riport setup-típusonként:
- találati arány;
- átlagos realizált R;
- expectancy;
- profit factor;
- max losing streak;
- MFE/MAE;
- rezsim szerinti bontás.

Score-súly csak legalább 100 lezárt, adatminőségileg megfelelő setup után módosítható.
