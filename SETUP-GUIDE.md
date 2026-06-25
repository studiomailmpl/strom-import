# STRØM Import — Komplet Setup-guide

Denne guide tager dig igennem alle trin for at få STRØM Import kørende lokalt.

**Forudsætninger:** macOS, en browser, og en terminal.

---

## Trin 1: Installér Homebrew

Homebrew er en package manager til macOS. Du bruger den til at installere Python og Node.

Åbn Terminal og kør:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Følg instruktionerne på skærmen. Når den er færdig, kør de kommandoer den viser dig under "Next steps" — typisk noget i stil med:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Verificér at det virker:

```bash
brew --version
```

---

## Trin 2: Installér Python 3.11+ og Node.js

```bash
brew install python@3.11 node
```

Verificér:

```bash
python3 --version   # Skal vise 3.11+
node --version       # Skal vise 18+
npm --version
```

---

## Trin 3: Database (allerede klart)

Vi bruger Neon (cloud PostgreSQL). Connection string er allerede sat op i din `.env`.

Du kan tjekke din database på: https://console.neon.tech

---

## Trin 4: Backend — Python virtual environment

```bash
cd ~/Downloads/strom-import-v2/backend

python3 -m venv venv
source venv/bin/activate
```

Du skal se `(venv)` i din terminal-prompt. Installér dependencies:

```bash
pip install -r requirements.txt
```

---

## Trin 5: Kør database-migration

Stadig i `backend/`-mappen med venv aktiveret:

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Den første kommando genererer en migration-fil baseret på dine SQLAlchemy-modeller. Den anden opretter alle tabellerne i Neon-databasen.

Verificér at det virkede — du bør se tabeller listet:

```bash
python3 -c "
from sqlalchemy import create_engine, inspect
from app.core.config import get_settings
engine = create_engine(get_settings().database_url_sync)
print(inspect(engine).get_table_names())
"
```

Forventet output: `['organisations', 'users', 'shopify_connections', 'imports', 'import_products']` (eller lignende).

---

## Trin 6: Anthropic API-nøgle

Du skal bruge en Anthropic API-nøgle til Claude Vision (PDF-parsing).

1. Gå til https://console.anthropic.com
2. Opret en konto eller log ind
3. Gå til "API Keys" og opret en ny nøgle
4. Kopiér nøglen og indsæt den i `backend/.env`:

```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxx
```

**Pris:** Claude Vision koster ca. $3-5 per 1000 sider. En typisk faktura-analyse koster under $0.01.

---

## Trin 7: Clerk-opsætning

Clerk er allerede konfigureret med API-nøgler i `.env`-filerne. Men du skal sikre at Organizations er aktiveret:

1. Gå til https://dashboard.clerk.com
2. Vælg dit projekt ("clean-spider-78")
3. Gå til **Organizations** i sidebaren
4. Slå **Enable Organizations** til
5. Under "Membership limit" — sæt den til f.eks. 50

---

## Trin 8: Frontend — Node dependencies

Åbn en **ny terminal-tab**:

```bash
cd ~/Downloads/strom-import-v2/frontend
npm install
```

---

## Trin 9: Start backend

I din backend-terminal (med venv aktiveret):

```bash
cd ~/Downloads/strom-import-v2/backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Du bør se:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
```

Test at API'en svarer: Åbn http://localhost:8000/docs i din browser — du skal se Swagger/OpenAPI-dokumentation.

---

## Trin 10: Start frontend

I din frontend-terminal:

```bash
cd ~/Downloads/strom-import-v2/frontend
npm run dev
```

Du bør se:

```
▲ Next.js 16.x.x
- Local: http://localhost:3000
```

Åbn http://localhost:3000 i din browser. Du skal se login-siden.

---

## Trin 11: Test hele flowet

1. **Log ind** via Clerk (opret en konto eller brug Google/GitHub)
2. Du lander på dashboard → onboarding-wizard starter
3. **Skip Shopify-forbindelse** indtil videre (du kan tilføje det senere)
4. **Sæt EUR-rate** (7.46) og **markup** (2.5)
5. Gå til **Import** og upload en PDF-faktura
6. Se AI-analysen køre
7. Review produkterne, godkend dem
8. (Push til Shopify kræver Shopify-forbindelse — se næste trin)

---

## Trin 12: Shopify-app (valgfrit, til push)

For at pushe produkter til Shopify skal du oprette en Custom App:

1. Gå til https://partners.shopify.com og log ind (eller opret konto)
2. Opret en ny app → vælg "Custom app"
3. Under **App setup**:
   - App URL: `http://localhost:3000`
   - Allowed redirection URL: `http://localhost:8000/api/v1/shopify/callback`
4. Kopiér **API key** og **API secret key**
5. Opdatér `backend/.env`:

```
SHOPIFY_API_KEY=din_api_key
SHOPIFY_API_SECRET=din_api_secret
```

6. Genstart backend (Ctrl+C → kør uvicorn igen)
7. I appen: Gå til Shopify-siden og forbind din butik

---

## Fejlfinding

### "ModuleNotFoundError" ved start af backend
→ Er du i venv? Kør `source venv/bin/activate` først.

### "Connection refused" til database
→ Tjek at din DATABASE_URL i `.env` matcher Neon-connection string.

### Frontend viser 500-fejl
→ Tjek at backend kører på port 8000. Se terminaloutput for fejl.

### "CORS error" i browser-konsollen
→ Tjek at `CORS_ORIGINS` i backend `.env` inkluderer `http://localhost:3000`.

### Alembic "Target database is not up to date"
→ Kør `alembic upgrade head` igen.

### Clerk login virker ikke
→ Tjek at `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` i `frontend/.env.local` er korrekt.
