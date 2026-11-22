# DémarcheClair — démarche CNIE

Site qui explique la démarche CNIE en français et en arabe : fiches vérifiées, recherche et assistant (chatbot).
À l'ouverture : **Espace usager** (libre, sans compte) ou **Espace administrateur** (identifiant + mot de passe).

## Structure
```
demarcheclair/
├── fiches/            # fiches JSON + schema.json
├── db/                # init.sql (tables) + charger_fiches.py
├── docker-compose.yml # base de données locale (PostgreSQL + pgvector)
├── validate_fiches.py # vérifie que les fiches respectent le schéma
├── backend/           # API FastAPI (main.py) + back-office (admin.py, creer_admin.py)
├── frontend/          # site (index.html) + espace admin (admin.html)
├── rag/               # chatbot
└── veille/            # surveillance des pages officielles
```

## Installation (première fois)
```bash
python -m venv venv
.\venv\Scripts\Activate.ps1        # Linux/macOS : source venv/bin/activate
pip install -r requirements.txt
```
Copier `.env.example` en `.env` et le remplir :
```
GEMINI_API_KEY=ta_cle
LLM_MODEL=gemini-3.5-flash-lite
ADMIN_IDENTIFIANT=ton_identifiant
ADMIN_MOT_DE_PASSE=ton_mot_de_passe
SEULEMENT_PUBLIE=false
```

## Démarrer
Docker Desktop doit être ouvert.
```bash
docker compose up -d
python validate_fiches.py
python db/charger_fiches.py
python -m uvicorn backend.main:app --reload
```
Dans un 2e terminal :
```bash
cd frontend
python -m http.server 5173
```
Site : http://localhost:5173 · API : http://127.0.0.1:8000/docs

## Espace administrateur
- Premier compte créé au démarrage depuis le `.env` (s'il n'existe aucun admin).
- Ajouter un admin ou changer un mot de passe : `python -m backend.creer_admin`
- Circuit : **brouillon → relu → publié**.
- « Marquer comme relu » met la date de vérification à aujourd'hui.
- Toute modification crée une nouvelle version et renvoie la fiche en brouillon.
- Renvoyer ou dépublier demande un commentaire ; chaque action est tracée (table `validations`).
- Chaque action met à jour la base **et** le fichier `fiches/*.json` : committer les fichiers modifiés.
- Avec `SEULEMENT_PUBLIE=true`, le site et le chatbot n'utilisent que les fiches publiées.

