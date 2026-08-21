# BYBIT EU SWING RADAR — BACKEND SPECIFIKÁCIÓ

## 1. Cél
Folyamatosan gyűjtött Bybit EU és Coinalyze adatokból:
1. felépíteni a kereskedhető univerzumot;
2. előszűrni a likvid piacokat;
3. technikai core feature-öket és külön derivatíva-contextet számítani;
4. long/short jelölteket rangsorolni;
5. állapotváltozáskor riasztást létrehozni;
6. a GPT-nek rövid, időbélyegzett, auditálható JSON-t adni.

## 2. Végrehajtási invariánsok
- Kizárólag Bybit EU, kizárólag USDC jegyzésű spot instrumentum.
- Long végrehajtás: kizárólag USDC spot.
- Short végrehajtás: kizárólag USDC spot-margin short, ha az exact USDC instrumentum margin-képes, a base asset publikus borrowability ellenőrzése pozitív és `shortable=true`.
- Perpetual/futures/egyéb derivatíva nem végrehajtási piac.
- Coinalyze/derivatíva-adat kizárólag context/conviction enrichment. Nem bizonyít spot végrehajthatóságot és nem lehet hard gate.
- Hiányzó vagy degradált OI/funding/liquidation adat önmagában nem változtathat strict TRADE setupot NO-TRADE-re, és nem emelhet NO-TRADE setupot strict TRADE-re.

## 3. Források

### Bybit EU
Base URL: `https://api.bybit.eu`

Nyilvános V5 végpontok:
- `/v5/market/time`
- `/v5/market/instruments-info`
- `/v5/market/tickers`
- `/v5/market/kline`
- `/v5/market/orderbook`
- `/v5/market/recent-trade`

Végrehajtási ellenőrzés:
- exact USDC spot instrumentum;
- pair-level margin flag;
- publikus Spot Margin borrowability és max borrowing amount;
- a tényleges inventory/borrow cost belépéskor újraellenőrzendő.

### Coinalyze
Base URL: `https://api.coinalyze.net/v1`
Auth header: `api_key`
Rate limit: 40 symbol-call/perc/API-key. Egy kérés legfeljebb 20 szimbólumot tartalmazhat, és minden szimbólum külön symbol-callnak számít.

A jelenlegi swing enrichment által használt végpontok:
- `/future-markets`
- `/open-interest`
- `/funding-rate`
- `/open-interest-history`
- `/liquidation-history`

További, későbbi context-forrásként használható:
- `/predicted-funding-rate`
- `/funding-rate-history`
- `/predicted-funding-rate-history`
- `/long-short-ratio-history`
- `/ohlcv-history`

## 4. Adatgyűjtési ütemezés
- Bybit tickers: 1 perc.
- Bybit 1H kline frissítés: 5 perc; csak lezárt gyertyát használj.
- Bybit 4H kline: óránként és 4H zárás után.
- Bybit 1D kline: óránként és napi zárás után.
- Coinalyze enrichment: csak a top, konfigurált számú jelöltre; rate-limit aware módon.
- Teljes core score: óránként.
- Mély scan: minden lezárt 4H gyertyánál.
- Universe refresh: naponta és listázási változáskor.

## 5. Kétlépcsős szűrés

### Stage A — Bybit EU core scan
A strict döntéshez szükséges core score-ok kizárólag ebből a rétegből származnak.

Kizárás/execution gate:
- nem Trading státusz;
- nem USDC quote;
- stable/fiat base;
- konfigurált minimum alatti turnover;
- konfigurált maximum feletti spread;
- elégtelen történeti adat;
- short oldalon nem ellenőrzött USDC spot-margin borrowability;
- RR < 2,0.

Core rangsor/feature-ek:
- turnover és spread;
- ATR/BB kompresszió;
- relatív volumen;
- 4H range/breakout közelség;
- BTC relatív erő;
- 1D/4H trendstruktúra.

### Stage B — Coinalyze context enrichment
Csak context/conviction, nem strict gate.

A top jelöltekre:
- current OI;
- OI változás 1H/4H/24H;
- current funding;
- liquidation flow;
- venue/quote provenance;
- adatminőség és endpointonkénti coverage.

Egy Coinalyze endpoint hibája nem nullázhatja le automatikusan a többi sikeres context-adatot. A részleges eredményt meg kell tartani, az exact upstream hibát pedig a data-statusban ki kell írni.

## 6. Core feature-k és pontozás

### Core Expansion Score 0–100
A jelenlegi worker core számítása:
- ATR-kompresszió: 25%;
- Bollinger-width kompresszió: 25%;
- range-boundary közelség: 20%;
- 4H volume komponens: 15%;
- range maturity: 15%.

OI, funding és liquidation **nem módosíthatja ezt a strict gate score-t**.

### Core Direction Score -100…+100
A jelenlegi worker core elemei:
- 4H trendstruktúra;
- 1D trendstruktúra;
- BTC-relative 20x4H erő/gyengeség;
- pozíció a 20-bar range-ben;
- legutóbbi lezárt 4H gyertya iránya és volume-contextje.

OI/funding/crowding értelmezhető külön contextként, de nem módosíthatja a strict direction score-t.

### Core Quality Score 0–100
A jelenlegi worker core elemei:
- turnover;
- spread;
- directional confluence;
- trigger proximity/clarity;
- core adatkomponens;
- RR komponens.

Coinalyze availability nem adhat vagy vehet el strict quality pontot.

### Setup Score
`0.35 * expansion_score + 0.35 * abs(direction_score) + 0.30 * quality_score`

A setup score kizárólag a fenti core score-okból készül.

### Strict minimumok
- setup score >= 70 az ARMED/TRIGGERED prioritáshoz;
- expansion >= 55;
- abs(direction) >= 35;
- quality >= 60;
- RR >= 2,0;
- execution/liquidity gate-ek teljesülnek.

A Coinalyze context hiánya **nem** szerepel a strict gate-ek között.

## 7. Derivatíva-context értelmezés
Ha rendelkezésre áll:
- OI delta 1H/4H/24H;
- price–OI quadrant;
- funding/crowding;
- long és short liquidation flow;
- venue provenance (`is_bybit_specific`, exchange, quote).

Ezek használhatók:
- conviction növelésére/csökkentésére;
- crowding/squeeze kockázat leírására;
- stresszteszt kiegészítésére;
- későbbi kutatáshoz/backtesthez.

Nem használhatók:
- strict score közvetlen módosítására;
- spot execution bizonyítására;
- önálló TRADE/NO-TRADE gate-ként.

## 8. Setup állapotgép és trigger
- WATCH: core setup még nem strict/armed vagy trigger nincs megerősítve.
- ARMED: core score és gate-ek megfelelőek, trigger közel van.
- TRIGGERED: az API által szolgáltatott lezárt gyertyás trigger teljesült.
- MANAGED: nyitott setup követése, ha később pozíciókövetés készül.
- INVALIDATED: invalidation feltétel teljesült.
- EXPIRED: trigger nélkül lejárt vagy core score a szükséges szint alá esett.

A jelenlegi swing worker authoritative triggerje 4H lezárt gyertya a 20-bar range boundary felett/alatt. 1H csak későbbi/opcionális trigger-refinement lehet; a GPT nem találhat ki kötelező 1H gate-et, ha azt az API nem adja.

## 9. Coinalyze coverage és diagnosztika
- Coverage nevezője a ténylegesen enrichmentre kiválasztott `targeted` szimbólumok száma, nem a teljes deep-scan universe.
- A teljes analyzed universe méretét külön mezőben kell közölni.
- `GOOD` Coinalyze source-quality csak akkor adható, ha a targetált kör teljes és nincs endpoint-hiba.
- Részleges endpoint-hiba esetén a sikeres symbol/context payload maradjon elérhető, source-quality legyen PARTIAL.
- Az exact upstream hiba kerüljön a data-status `missing_fields`/diagnostic mezőjébe.

## 10. Public API a GPT Action számára
- `GET /health`
- `GET /v1/scan?direction=both&limit=3&min_score=70`
- `GET /v1/market-regime`
- `GET /v1/setup/{symbol}`
- `GET /v1/watchlist?limit=10`
- `GET /v1/alerts?since=...&limit=20`
- `GET /v1/data-status`

Az endpointok gyorsítótárból olvassanak. A GPT kérésére ne induljon teljes piaci scan.

## 11. Biztonság
- Bybit kulcs kizárólag read-only.
- Nincs order-create, order-amend, order-cancel vagy withdrawal endpoint.
- Kulcsok csak szerveroldali secret store-ban.
- GPT Action saját `X-Radar-Key` kulccsal hitelesít.
- Rate limit, request logging, IP/abuse védelem.
- JSON válaszban soha nincs upstream API-kulcs.
- Minden adat UTC-ben tárolódik, válaszban UTC és Europe/Budapest idő is szerepel.

## 12. Backtest és audit
Mentsd:
- scan időpont;
- core feature snapshot;
- core score-ok;
- külön derivatives-context snapshot és availability;
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

Core score-súly csak legalább 100 lezárt, adatminőségileg megfelelő setup után módosítható. Derivatíva-context súly vagy gate csak külön kutatás/backtest és explicit verzióváltás után vezethető be.

## 13. Day-trade v0.7.3 kiegészítés
Ez a fejezet kizárólag a day-trade motorra vonatkozik; a fenti swing 1D/4H trigger-szabályokat nem módosítja.

- Stratégia verzió: `0.7.3`. A külön derivatives Flow feature verziója változatlanul `0.7.2.2`.
- Authoritative day-trade trigger: lezárt 5m liquidity sweep -> reclaim -> 5m local structure shift -> a confirmation időpontjáig teljesen lezárt 15m nem-ellenirányú struktúra -> volume confirmation.
- Entry: a 5m structure-confirmation gyertya záróára. Stop/invalidation: a sweep extreme.
- A 4H `timeframe_conflict` diagnosztikai/context mező marad, de nem hard-veto a strict eligibilityre vagy executionre. A 4H technikai komponensek továbbra is részt vehetnek a direction/context számításban.
- Változatlan hard gate-ek: USDC-only execution, spot long, exact USDC spot-margin borrowable short, likviditás/spread, core score minimumok, költség utáni RR és structural-barrier/target-path.
- A korábbi egyszerű 12x5m breakout önmagában nem adhat `TRADE` döntést. WATCH/ARMED státusz előre jelezhet közelgő setupot, de `TRIGGERED/TRADE` csak a teljes sweep-confirmation szekvenciával lehetséges.
- Journal és historical replay `strategy_version=0.7.3`, ezért a v0.7.2 mintákkal nem keverhető.
- OI/funding/Flow context-only marad, nem módosítja a strict gate-eket.

## 14. Day-trade v0.7.4 kiegészítés
A v0.7.3 történeti szemantikája változatlanul sweep-only és reprodukálható marad. A v0.7.4 új, külön kohorsz; a score-, RR-, target-path- és execution gate-eket nem lazítja.

- Stratégia verzió: `0.7.4`. A derivatives Flow feature verziója változatlanul `0.7.2.2`.
- Authoritative live trigger két lezárt-5m útvonal egyike: (1) az előző 12 lezárt 5m gyertya range-boundaryjének közvetlen close-breakout/breakdownja (`CLOSED_5M_RANGE_BREAKOUT`); vagy (2) a v0.7.3-ból megtartott liquidity sweep -> reclaim -> 5m structure shift -> nem ellenirányú lezárt 15m struktúra -> volume confirmation (`LIQUIDITY_SWEEP_RECLAIM`).
- Ha mindkét út egyszerre érvényes, a sweep útvonal prioritást kap, mert annak entry/invalidation geometriája specifikusabb.
- A direct impulse breakout önmagában nem kerülheti meg a STRICT gate-eket: setup >= 70, expansion >= 55, side-direction >= 35, quality >= 65, költség utáni RR >= 1.8, valid target path, likviditás/spread, valamint execution gate kötelező.
- Long végrehajtás továbbra is kizárólag Bybit EU USDC spot. Short kizárólag igazolt Bybit EU USDC spot-margin borrowability mellett.
- OI/funding/Flow továbbra is context-only és nem hard gate.
- Journal és historical replay `strategy_version=0.7.4`; a v0.7.3 sorok és backtest jobok változatlanul elkülönítve maradnak.
- A v0.7.3 prospective sweep-funnel kutatási kohorsz változatlanul `v073-prospective-funnel-v1`; nem kerül visszamenőleg átértelmezésre v0.7.4-ként.

## 15. Day-trade v0.7.5 breakout-aktiváció
A v0.7.4 crossing-bar-only impulse trigger történeti szemantikája változatlanul reprodukálható. A v0.7.5 külön live stratégia-kohorsz.

- A `CLOSED_5M_RANGE_BREAKOUT` esemény az eredeti breakout gyertyán és az azt közvetlenül követő egy lezárt 5m gyertyán aktív marad (`validity_bars=2`), amennyiben az eredeti 12-bar range boundary továbbra is tart.
- A következő lezárt 5m gyertya puszta megjelenése nem törölheti a már valid breakout ajánlást. Hard invalidáció csak az eredeti boundary elvesztése vagy a meglévő STRICT score/RR/target-path/liquidity/execution gate hibája lehet.
- A triggerár az eredeti breakout boundaryhez marad horgonyozva; a rolling 12-bar high/low nem ratchetelheti el az aktív eventet a következő gyertyán.
- Journal deduplikáció az eredeti breakout event gyertyájához kötött, ezért a follow-through gyertya nem hozhat létre duplikált signalt.
- Historical replay ugyanazt a direct breakout + egy follow-through gyertya triggerutat használja.
- Journal és historical replay `strategy_version=0.7.5`; a v0.7.3 és v0.7.4 korábbi kohorszok nem kerülnek visszamenőleg átértelmezésre.
- USDC-only execution, spot long, igazolt USDC spot-margin short és context-only derivatives invariáns változatlan.

## 16. Day-trade v0.7.6 setup/entry szétválasztás
A v0.7.5 történeti kohorsz változatlanul reprodukálható és `strategy_version=0.7.5` alatt marad. A v0.7.6 külön live stratégia-kohorsz, amely a technikai setup létezését elválasztja a pillanatnyi entry végrehajthatóságától.

- `setup_state=VALID` kizárólag a technikai setup minimumokat jelenti: setup score, expansion, side-direction és quality. A pillanatnyi RR, structural barrier vagy execution availability nem törölheti magát a technikai setupot.
- A pillanatnyi belépő külön `entry_state` mezőben jelenik meg: `ENTRY_CONFIRMED`, `ENTRY_PROVISIONAL`, `ENTRY_TOO_EXTENDED`, `BLOCKED_BY_BARRIER`, `RR_NOT_READY`, `WAIT_TRIGGER`, `EXECUTION_BLOCKED` vagy `NO_SETUP`.
- `TRADE` kizárólag `ENTRY_CONFIRMED` mellett adható, a meglévő USDC-only, liquidity/spread, target-path és nettó RR gate-ek teljesülése után. `ENTRY_PROVISIONAL` nem automatikus végrehajtási engedély.
- A direct breakout technikai setup-kontextusa nem jár le fixen két 5m gyertya után. Az első valódi crossing az uninterrupted breakout szekvencia originje, és addig marad aktív, amíg minden későbbi lezárt 5m gyertya tartja az eredeti boundaryt.
- A continuation gyertya új rolling 12-bar high/low áttörése **nem ratchetelheti** az aktív breakout origint. Új origin csak az eredeti boundary elvesztése után létrejövő új, független crossingból keletkezhet.
- A régi breakout boundary setup-origin/context. A pillanatnyi belépő geometriát `reference_entry` alapján frissen kell újraszámítani: entry zone, hard stop, targetek, structural barrier, target path és költség utáni RR.
- Structural barrier vagy gyenge pillanatnyi RR a jelenlegi entryt blokkolhatja, de egyébként technikailag valid setupot nem írhat át `NO_SETUP`/„nincs long/short setup” állapotra.
- `ENTRY_TOO_EXTENDED` esetén a setup valid marad, de chase helyett friss retest/pullback entry szükséges.
- A kockázati `hard_stop` intrabar touch/cross alapon aktiválódik; `requires_candle_close=false`. A hard stophoz nem kell lezárt 5m gyertyát megvárni.
- A magasabb idősíkú thesis/structure invalidation külön mező: például longnál a 15m higher-low struktúra elvesztése. Ez nem azonos a hard stoppal.
- A 4H `timeframe_conflict` továbbra is context-only, és önmagában nem rejthet el technikailag valid day setupot.
- A lezárt 5m breakout/sweep továbbra is authoritative megerősített entry út. A 15s/30s, 5s microstructure bucket-alapú intrabar acceptance külön, prospektív research-only összehasonlítás; kimenetele validáció nélkül nem emelhető live `TRADE` gate-té.
- A missed-move audit outcome-bearing, offline kutatási réteg. A döntéskori blocker állapotot változatlanul rögzíti, majd később MFE/MAE és +1/+2/+3% favorable move alapján méri a false-negative költséget. Ez önmagában nem jogosít retrospektív threshold-optimalizálásra.
- Long végrehajtás továbbra is kizárólag Bybit EU USDC spot. Short kizárólag igazolt Bybit EU USDC spot-margin borrowability mellett. OI/funding/liquidation/Flow továbbra is context-only és nem hard gate.
- A v0.7.3, v0.7.4 és v0.7.5 journal/backtest/research kohorszok immutábilisak; a v0.7.6 nem végez történeti backfillt vagy átértelmezést ezekbe.
