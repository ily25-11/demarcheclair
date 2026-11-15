# DémarcheClair — démarche CNIE

## Structure
```
demarcheclair/
├── fiches/            # fiches JSON + schema.json
├── db/init.sql        # schéma PostgreSQL + pgvector
├── docker-compose.yml # base de données locale
├── validate_fiches.py # vérifie que les fiches respectent le schéma
├── backend/           # API (binôme B, Fille 3)
├── frontend/          # interface 
├── rag/               # chatbot RAG
└── veille/            # scripts de veille / scraping 
```

## Démarrer
```bash
docker compose up -d
pip install requirement
python validate_fiches.py
python -m uvicorn backend.main:app --reload
python -m http.server 5173
```

