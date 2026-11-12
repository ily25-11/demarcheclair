"""Charge les fiches JSON (dossier fiches/) dans PostgreSQL.

- valide chaque fiche contre fiches/schema.json
- insère la fiche si elle n'existe pas encore
- si elle existe et que son contenu a changé: archive l'ancienne version
  dans fiche_versions et incrémente le numéro de version

Usage (depuis la racine du projet):
    pip install -r requirements.txt
    python db/charger_fiches.py
"""
import json
import os
import sys
from pathlib import Path

import psycopg2
from jsonschema import Draft202012Validator
from psycopg2.extras import Json

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_FICHES = RACINE / "fiches"

# Mêmes valeurs que docker-compose.yml (modifiables via variables d'environnement)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "demarche"),
    "password": os.getenv("DB_PASSWORD", "demarche"),
    "dbname": os.getenv("DB_NAME", "demarcheclair"),
}


def sans_version(contenu: dict) -> dict:
    """Copie du contenu sans le champ 'version' (pour comparer le fond)."""
    return {k: v for k, v in contenu.items() if k != "version"}


def charger_fiche(cur, fiche: dict) -> str:
    cur.execute(
        "SELECT id, version, contenu FROM fiches WHERE slug = %s AND langue = %s",
        (fiche["slug"], fiche["langue"]),
    )
    existante = cur.fetchone()

    if existante is None:
        cur.execute(
            """
            INSERT INTO fiches
                (slug, langue, demarche, scenario, titre, contenu,
                 statut, date_derniere_verification, version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                fiche["slug"], fiche["langue"], fiche["demarche"], fiche["scenario"],
                fiche["titre"], Json(fiche), fiche["statut"],
                fiche["date_derniere_verification"], fiche["version"],
            ),
        )
        fiche_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO fiche_versions (fiche_id, version, contenu) VALUES (%s, %s, %s)",
            (fiche_id, fiche["version"], Json(fiche)),
        )
        return "créée"

    fiche_id, version_actuelle, contenu_actuel = existante
    if sans_version(contenu_actuel) == sans_version(fiche):
        return "inchangée"

    nouvelle_version = version_actuelle + 1
    fiche_a_jour = {**fiche, "version": nouvelle_version}
    cur.execute(
        """
        UPDATE fiches
        SET demarche = %s, scenario = %s, titre = %s, contenu = %s,
            statut = %s, date_derniere_verification = %s,
            version = %s, updated_at = now()
        WHERE id = %s
        """,
        (
            fiche["demarche"], fiche["scenario"], fiche["titre"], Json(fiche_a_jour),
            fiche["statut"], fiche["date_derniere_verification"],
            nouvelle_version, fiche_id,
        ),
    )
    cur.execute(
        "INSERT INTO fiche_versions (fiche_id, version, contenu) VALUES (%s, %s, %s)",
        (fiche_id, nouvelle_version, Json(fiche_a_jour)),
    )
    return f"mise à jour (v{nouvelle_version})"


def main() -> int:
    schema = json.loads((DOSSIER_FICHES / "schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    fichiers = sorted(p for p in DOSSIER_FICHES.glob("*.json") if p.name != "schema.json")
    if not fichiers:
        print("Aucune fiche trouvée dans fiches/")
        return 1

    fiches = []
    for fichier in fichiers:
        fiche = json.loads(fichier.read_text(encoding="utf-8"))
        problemes = list(validator.iter_errors(fiche))
        if problemes:
            print(f"❌ {fichier.name} invalide, rien n'est chargé:")
            for p in problemes:
                print(f"   - {list(p.path)}: {p.message}")
            return 1
        fiches.append((fichier.name, fiche))

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:  # 'with conn' = commit auto, rollback si erreur
            for nom, fiche in fiches:
                print(f"✅ {nom}: {charger_fiche(cur, fiche)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
