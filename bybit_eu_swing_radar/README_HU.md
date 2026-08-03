# Bybit EU Swing Radar — induló csomag

Ez a csomag egy **read-only, háttérben futó swing scanner + egyedi GPT elemzőréteg** specifikációja.

## Tartalom
- `agent/AGENT_INSTRUCTIONS_HU.md` — bemásolható GPT-instrukció.
- `action/openapi.yaml` — a saját Radar API GPT Action sémája.
- `BACKEND_SPEC_HU.md` — adatgyűjtés, feature-k, scoring, állapotgép.
- `DATABASE_SCHEMA.sql` — PostgreSQL alap séma.
- `SCORING_RULES.json` — géppel olvasható súlyok és küszöbök.
- `backend/` — minimális FastAPI read-only API scaffold.

## Helyes architektúra
1. A háttérszkenner lekéri és eltárolja a Bybit EU + Coinalyze adatokat.
2. A scanner óránként és 4H záráskor újraszámolja a setupokat.
3. A FastAPI gyorsítótárból/adatbázisból szolgálja ki az eredményeket.
4. Az egyedi GPT kizárólag ezt a saját API-t hívja.
5. Sem a GPT, sem az API nem küld ordert.

## Beállítás
1. Telepíts PostgreSQL-t.
2. Futtasd a `DATABASE_SCHEMA.sql` fájlt.
3. Másold a `backend/.env.example` fájlt `.env` néven.
4. Add meg a Coinalyze API kulcsot és egy saját `RADAR_API_KEY` értéket.
5. Indítsd:
   ```bash
   cd backend
   pip install -r requirements.txt
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
6. Tedd HTTPS mögé.
7. Az `action/openapi.yaml` szerver URL-jét cseréld le.
8. A GPT szerkesztőben adj hozzá Actiont API-key hitelesítéssel:
   - header neve: `X-Radar-Key`
   - értéke: a `RADAR_API_KEY`
9. Másold be az agent instrukciót.

## Fontos
A scaffold nem teljes scanner-motor. A végpontok és az adatmodellek készen állnak, de a folyamatos adatgyűjtő, feature-számító és backtest worker implementációját még hozzá kell építeni. A rendszer csak akkor ad valós setupot, ha az adatbázisban valóban friss, ellenőrzött scan-eredmény van.
