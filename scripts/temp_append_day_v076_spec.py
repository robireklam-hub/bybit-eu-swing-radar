from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "bybit_eu_swing_radar" / "BACKEND_SPEC_HU.md"
text = path.read_text(encoding="utf-8")
heading = "## 16. Day-trade v0.7.6 setup/entry szétválasztás"
if heading in text:
    raise SystemExit("v0.7.6 annex already present")

annex = r'''

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
'''

path.write_text(text.rstrip() + annex + "\n", encoding="utf-8")
print("v0.7.6 backend spec annex appended")
