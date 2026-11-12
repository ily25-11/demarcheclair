# DémarcheClair — démarche CNIE

## Structure
```
demarcheclair/
├── fiches/            # fiches JSON (binôme A) + schema.json (contrat entre binômes)
├── db/init.sql        # schéma PostgreSQL + pgvector
├── docker-compose.yml # base de données locale
├── validate_fiches.py # vérifie que les fiches respectent le schéma
├── backend/           # API (binôme B, Fille 3)
├── frontend/          # interface (binôme B, Fille 4)
├── rag/               # chatbot RAG (binôme A, Fille 2)
└── veille/            # scripts de veille / scraping (binôme A, Fille 1)
```

## Démarrer
```bash
docker compose up -d
pip install jsonschema
python validate_fiches.py
```

## Règles Git
- `main` = stable, on ne pousse jamais dessus directement
- une branche par personne: `fille1-contenu`, `fille2-rag`, `fille3-backend`, `fille4-frontend`
- Pull Request relue par l'autre fille du binôme
