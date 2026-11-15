"""API DémarcheClair (FastAPI).

Lancer depuis la racine du projet:
    python -m uvicorn backend.main:app --reload
Puis ouvrir http://127.0.0.1:8000/docs
"""
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from typing import Literal, Optional

import traceback
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

from rag.chatbot import ConfigError, repondre

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # clés d'API (jamais sur Git)

# Mêmes valeurs que docker-compose.yml (modifiables via variables d'environnement)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "demarche"),
    "password": os.getenv("DB_PASSWORD", "demarche"),
    "dbname": os.getenv("DB_NAME", "demarcheclair"),
}

# En dev on voit toutes les fiches. En production: SEULEMENT_PUBLIE=true
SEULEMENT_PUBLIE = os.getenv("SEULEMENT_PUBLIE", "false").lower() == "true"
FILTRE_STATUT = " AND statut = 'publie'" if SEULEMENT_PUBLIE else ""

Langue = Literal["fr", "ar"]


@contextmanager
def curseur():
    """Ouvre une connexion, la valide (commit) à la fin, puis la ferme."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # pg_trgm sert à la recherche tolérante aux fautes de frappe (F1)
    with curseur() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        # Journal des recherches (sans donnée personnelle): sert au tableau de bord qualité
        cur.execute("""CREATE TABLE IF NOT EXISTS recherches (
            id SERIAL PRIMARY KEY, q TEXT NOT NULL, langue TEXT,
            nb_resultats INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    yield


app = FastAPI(title="DémarcheClair API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def ajouter_fraicheur(ligne: dict) -> dict:
    """Ajoute 'jours_depuis_verification' pour le badge « vérifié il y a X jours »."""
    d = ligne.get("date_derniere_verification")
    ligne["jours_depuis_verification"] = (date.today() - d).days if d else None
    ligne["date_derniere_verification"] = d.isoformat() if d else None
    return ligne


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fiches")
def lister_fiches(langue: Langue = "fr"):
    with curseur() as cur:
        cur.execute(
            f"""
            SELECT slug, langue, scenario, titre,
                   contenu->>'resume' AS resume,
                   statut, date_derniere_verification, version
            FROM fiches
            WHERE langue = %s {FILTRE_STATUT}
            ORDER BY scenario, slug
            """,
            (langue,),
        )
        return [ajouter_fraicheur(dict(r)) for r in cur.fetchall()]


@app.get("/fiches/{slug}")
def lire_fiche(slug: str, langue: Langue = "fr"):
    with curseur() as cur:
        cur.execute(
            f"""
            SELECT slug, langue, scenario, titre, contenu,
                   statut, date_derniere_verification, version
            FROM fiches
            WHERE slug = %s AND langue = %s {FILTRE_STATUT}
            """,
            (slug, langue),
        )
        ligne = cur.fetchone()
    if ligne is None:
        raise HTTPException(status_code=404, detail="Fiche introuvable")
    return ajouter_fraicheur(dict(ligne))


@app.get("/search")
def rechercher(q: str = Query(..., min_length=2, max_length=100), langue: Langue = "fr"):
    """Recherche par mot-clé, tolérante aux fautes (pg_trgm)."""
    motif = f"%{q}%"
    with curseur() as cur:
        cur.execute(
            f"""
            SELECT slug, langue, scenario, titre,
                   contenu->>'resume' AS resume,
                   statut, date_derniere_verification, version,
                   GREATEST(word_similarity(%(q)s, titre),
                            word_similarity(%(q)s, slug || ' ' || scenario) * 0.9,
                            word_similarity(%(q)s, coalesce(contenu->>'resume', '')) * 0.8,
                            word_similarity(%(q)s, contenu::text) * 0.5) AS score
            FROM fiches
            WHERE langue = %(langue)s {FILTRE_STATUT}
              AND (titre ILIKE %(motif)s
                   OR contenu::text ILIKE %(motif)s
                   OR word_similarity(%(q)s, contenu::text) > 0.4)
            ORDER BY score DESC
            LIMIT 10
            """,
            {"q": q, "motif": motif, "langue": langue},
        )
        resultats = [ajouter_fraicheur(dict(r)) for r in cur.fetchall()]
        cur.execute("INSERT INTO recherches (q, langue, nb_resultats) VALUES (%s, %s, %s)",
                    (q.lower(), langue, len(resultats)))
        return resultats


class FeedbackIn(BaseModel):
    slug: str
    langue: Langue = "fr"
    utile: bool
    signalement: Optional[str] = Field(None, max_length=500)  # pas de données personnelles


@app.post("/feedback", status_code=201)
def envoyer_feedback(fb: FeedbackIn):
    with curseur() as cur:
        cur.execute("SELECT id FROM fiches WHERE slug = %s AND langue = %s", (fb.slug, fb.langue))
        fiche = cur.fetchone()
        if fiche is None:
            raise HTTPException(status_code=404, detail="Fiche introuvable")
        cur.execute(
            "INSERT INTO feedback (fiche_id, utile, signalement) VALUES (%s, %s, %s)",
            (fiche["id"], fb.utile, fb.signalement),
        )
    return {"ok": True}


@app.get("/fiches/{slug}/historique")
def historique(slug: str, langue: Langue = "fr"):
    """Versions d'une fiche, avec les champs modifiés par rapport à la version précédente."""
    with curseur() as cur:
        cur.execute(
            """SELECT v.version, v.contenu, v.created_at
               FROM fiche_versions v JOIN fiches f ON f.id = v.fiche_id
               WHERE f.slug = %s AND f.langue = %s ORDER BY v.version""",
            (slug, langue),
        )
        lignes = cur.fetchall()
    resultat = []
    for i, l in enumerate(lignes):
        prec = lignes[i - 1]["contenu"] if i else None
        modifies = [] if prec is None else [
            k for k in l["contenu"] if k != "version" and l["contenu"].get(k) != prec.get(k)]
        resultat.append({"version": l["version"], "date": l["created_at"].date().isoformat(),
                         "champs_modifies": modifies})
    return resultat[::-1]


@app.get("/stats")
def stats():
    """Tableau de bord qualité."""
    with curseur() as cur:
        cur.execute("""SELECT count(*) AS total,
                   count(*) FILTER (WHERE statut = 'publie') AS publiees,
                   count(*) FILTER (WHERE date_derniere_verification IS NULL
                                    OR date_derniere_verification < current_date - 90) AS a_reverifier
                   FROM fiches""")
        general = dict(cur.fetchone())
        cur.execute("""SELECT f.slug, f.langue,
                   count(*) FILTER (WHERE fb.utile) AS utile,
                   count(*) FILTER (WHERE NOT fb.utile) AS pas_utile
                   FROM fiches f JOIN feedback fb ON fb.fiche_id = f.id
                   GROUP BY f.slug, f.langue ORDER BY pas_utile DESC""")
        retours = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT q, count(*) AS fois FROM recherches WHERE nb_resultats = 0
                   GROUP BY q ORDER BY fois DESC LIMIT 10""")
        vides = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS n FROM recherches")
        total = cur.fetchone()["n"]
    return {**general, "recherches_total": total, "feedback": retours, "recherches_sans_resultat": vides}


class MessageIn(BaseModel):
    role: Literal["user", "assistant"]
    texte: str = Field(..., max_length=500)


class ChatIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    langue: Langue = "fr"
    historique: list[MessageIn] = Field(default_factory=list, max_length=6)


@app.post("/chat")
def chat(c: ChatIn):
    """Répond à une question uniquement à partir des fiches de la langue choisie."""
    with curseur() as cur:
        cur.execute(
            f"""SELECT slug, titre, contenu, date_derniere_verification
                FROM fiches WHERE langue = %s {FILTRE_STATUT} ORDER BY slug""",
            (c.langue,),
        )
        fiches = [dict(r) for r in cur.fetchall()]
    try:
        return repondre(c.question, fiches, [m.model_dump() for m in c.historique])
    except ConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        traceback.print_exc()  # l'erreur détaillée s'affiche dans le terminal d'uvicorn
        raise HTTPException(status_code=502, detail="Le service de réponse est indisponible.")
