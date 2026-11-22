"""Back-office DémarcheClair (exigence F10) : gestion et validation des fiches.

Circuit de validation (cahier des charges, section 9) :

    brouillon ──relire──▶ relu ──publier──▶ publie
        ▲                  │                  │
        └──── renvoyer ────┘                  │
        └──────────────── dépublier ──────────┘

- Toute modification du contenu crée une nouvelle version et renvoie la fiche en « brouillon » :
  elle doit être relue de nouveau avant d'être publiée.
- « Marquer relu » met la date de dernière vérification à aujourd'hui.
- Les fichiers fiches/*.json restent la référence (versionnés sur Git) : chaque action
  de l'admin met à jour la base ET le fichier JSON. Pensez à committer les fichiers modifiés.
- Chaque action est enregistrée dans la table « validations » (qui, quand, pourquoi).

Connexion : identifiant + mot de passe (table « admins », mots de passe hachés PBKDF2).
POST /admin/login renvoie un jeton de session (valable 8 h) à envoyer ensuite dans
l'en-tête « Authorization: Bearer <jeton> ». Le site public reste accessible sans compte.

Premier compte : ADMIN_IDENTIFIANT et ADMIN_MOT_DE_PASSE dans .env (créé au démarrage si
aucun admin n'existe), ou : python -m backend.creer_admin
"""
import hashlib
import json
import os
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from jsonschema import Draft202012Validator
from psycopg2.extras import Json
from pydantic import BaseModel, Field

# Import placé dans main.py APRÈS la définition de curseur() : pas d'import circulaire.
from backend.main import ajouter_fraicheur, curseur

DOSSIER_FICHES = Path(__file__).resolve().parent.parent / "fiches"
VALIDATEUR = Draft202012Validator(
    json.loads((DOSSIER_FICHES / "schema.json").read_text(encoding="utf-8")))

Langue = Literal["fr", "ar"]
Statut = Literal["brouillon", "relu", "publie"]

# (statut actuel, statut demandé) -> nom de l'action
TRANSITIONS = {
    ("brouillon", "relu"): "relecture",
    ("relu", "publie"): "publication",
    ("relu", "brouillon"): "renvoi",
    ("publie", "brouillon"): "depublication",
}
COMMENTAIRE_OBLIGATOIRE = {"renvoi", "depublication"}


# ---------- Sécurité : comptes et sessions ----------

DUREE_SESSION_H = 8
ITERATIONS = 200_000


def hacher_mot_de_passe(mdp: str) -> str:
    sel = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", mdp.encode(), sel, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${sel.hex()}${h.hex()}"


def verifier_mot_de_passe(mdp: str, stocke: str) -> bool:
    try:
        _, it, sel, h = stocke.split("$")
        calcule = hashlib.pbkdf2_hmac("sha256", mdp.encode(), bytes.fromhex(sel), int(it))
        return secrets.compare_digest(calcule.hex(), h)
    except (ValueError, TypeError):
        return False


def hash_jeton(jeton: str) -> str:
    """On ne stocke que l'empreinte du jeton : une fuite de la base ne donne pas de session."""
    return hashlib.sha256(jeton.encode()).hexdigest()


def admin_connecte(authorization: str = Header(default="")) -> str:
    """Dépendance FastAPI : renvoie l'identifiant de l'admin connecté, sinon 401."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Connexion requise")
    with curseur() as cur:
        cur.execute("""SELECT a.identifiant FROM admin_sessions s JOIN admins a ON a.id = s.admin_id
                       WHERE s.jeton_hash = %s AND s.expire_le > now()""",
                    (hash_jeton(authorization[7:]),))
        ligne = cur.fetchone()
    if ligne is None:
        raise HTTPException(401, "Session expirée, reconnectez-vous")
    return ligne["identifiant"]


# Routes publiques (connexion) et routes protégées (tout le reste)
auth_router = APIRouter(prefix="/admin", tags=["admin"])
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_connecte)])

# Anti force brute : 5 échecs -> identifiant bloqué 5 minutes (mémoire du processus)
ECHECS: dict = {}


class LoginIn(BaseModel):
    identifiant: str = Field(..., min_length=1, max_length=60)
    mot_de_passe: str = Field(..., min_length=1, max_length=200)


@auth_router.post("/login")
def login(l: LoginIn):
    cle = l.identifiant.strip().lower()
    nb, depuis = ECHECS.get(cle, (0, 0.0))
    if nb >= 5 and time.time() - depuis < 300:
        raise HTTPException(429, "Trop de tentatives. Réessayez dans 5 minutes.")
    with curseur() as cur:
        cur.execute("SELECT id, identifiant, mot_de_passe FROM admins WHERE lower(identifiant) = %s", (cle,))
        admin = cur.fetchone()
        if admin is None or not verifier_mot_de_passe(l.mot_de_passe, admin["mot_de_passe"]):
            ECHECS[cle] = (nb + 1 if time.time() - depuis < 300 else 1, time.time())
            raise HTTPException(401, "Identifiant ou mot de passe incorrect")
        ECHECS.pop(cle, None)
        jeton = secrets.token_urlsafe(32)
        cur.execute("DELETE FROM admin_sessions WHERE expire_le < now()")
        cur.execute(f"""INSERT INTO admin_sessions (jeton_hash, admin_id, expire_le)
                        VALUES (%s, %s, now() + interval '{DUREE_SESSION_H} hours')""",
                    (hash_jeton(jeton), admin["id"]))
    return {"jeton": jeton, "identifiant": admin["identifiant"], "expire_dans_h": DUREE_SESSION_H}


@auth_router.post("/logout")
def logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        with curseur() as cur:
            cur.execute("DELETE FROM admin_sessions WHERE jeton_hash = %s", (hash_jeton(authorization[7:]),))
    return {"ok": True}


def creer_ou_modifier_admin(cur, identifiant: str, mot_de_passe: str) -> str:
    cur.execute("""INSERT INTO admins (identifiant, mot_de_passe) VALUES (%s, %s)
                   ON CONFLICT (identifiant) DO UPDATE SET mot_de_passe = EXCLUDED.mot_de_passe
                   RETURNING (xmax = 0) AS cree""", (identifiant, hacher_mot_de_passe(mot_de_passe)))
    return "créé" if cur.fetchone()["cree"] else "mot de passe modifié"


def creer_tables(cur):
    """Appelée au démarrage de l'API (lifespan de main.py)."""
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (
        id           SERIAL PRIMARY KEY,
        identifiant  TEXT NOT NULL UNIQUE,
        mot_de_passe TEXT NOT NULL,              -- haché (PBKDF2), jamais en clair
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now())""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
        jeton_hash TEXT PRIMARY KEY,
        admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
        expire_le  TIMESTAMPTZ NOT NULL)""")
    # Premier compte depuis le .env, seulement s'il n'existe encore aucun admin
    ident, mdp = os.getenv("ADMIN_IDENTIFIANT", "").strip(), os.getenv("ADMIN_MOT_DE_PASSE", "")
    cur.execute("SELECT count(*) AS n FROM admins")
    if cur.fetchone()["n"] == 0:
        if ident and mdp:
            creer_ou_modifier_admin(cur, ident, mdp)
            print(f"Compte admin « {ident} » créé depuis le .env")
        else:
            print("Aucun compte admin : ajoutez ADMIN_IDENTIFIANT et ADMIN_MOT_DE_PASSE au .env "
                  "ou lancez  python -m backend.creer_admin")
    cur.execute("""CREATE TABLE IF NOT EXISTS validations (
        id          SERIAL PRIMARY KEY,
        fiche_id    INTEGER NOT NULL REFERENCES fiches(id) ON DELETE CASCADE,
        action      TEXT NOT NULL,
        de          TEXT,
        vers        TEXT,
        commentaire TEXT,
        par         TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now())""")


# ---------- Outils ----------

def chemin_fichier(slug: str, langue: str) -> Path:
    return DOSSIER_FICHES / f"{slug}.{langue}.json"


def ecrire_fichier(contenu: dict):
    chemin = chemin_fichier(contenu["slug"], contenu["langue"])
    chemin.write_text(json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valider_schema(contenu: dict):
    erreurs = [f"{'/'.join(map(str, e.path)) or '(racine)'} : {e.message}"
               for e in VALIDATEUR.iter_errors(contenu)]
    if erreurs:
        raise HTTPException(422, {"message": "La fiche ne respecte pas le schéma", "erreurs": erreurs})


def lire_ligne(cur, slug: str, langue: str) -> dict:
    cur.execute("SELECT * FROM fiches WHERE slug = %s AND langue = %s FOR UPDATE", (slug, langue))
    ligne = cur.fetchone()
    if ligne is None:
        raise HTTPException(404, "Fiche introuvable")
    return dict(ligne)


def journaliser(cur, fiche_id, action, de, vers, commentaire=None, par=None):
    cur.execute("""INSERT INTO validations (fiche_id, action, de, vers, commentaire, par)
                   VALUES (%s, %s, %s, %s, %s, %s)""", (fiche_id, action, de, vers, commentaire, par))


def sans_version(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "version"}


def avertissements(contenu: dict) -> list:
    """Points à signaler au relecteur (n'empêchent pas la publication)."""
    av = []
    if contenu.get("points_a_verifier"):
        av.append(f"{len(contenu['points_a_verifier'])} point(s) à vérifier non tranché(s)")
    d = contenu.get("date_derniere_verification")
    if not d:
        av.append("Jamais vérifiée")
    elif (date.today() - date.fromisoformat(d)).days > 90:
        av.append("Dernière vérification il y a plus de 90 jours")
    return av


# ---------- Modèles ----------

class StatutIn(BaseModel):
    statut: Statut
    commentaire: Optional[str] = Field(None, max_length=500)


class ContenuIn(BaseModel):
    contenu: dict


# ---------- Routes ----------

@router.get("/moi")
def moi(identifiant: str = Depends(admin_connecte)):
    """Qui est connecté (sert à vérifier la session au chargement de la page)."""
    return {"identifiant": identifiant}


@router.get("/fiches")
def lister(statut: Optional[Statut] = None, langue: Optional[Langue] = None):
    """Toutes les fiches, quel que soit leur statut (le site public peut n'afficher que les publiées)."""
    with curseur() as cur:
        cur.execute("""
            SELECT f.slug, f.langue, f.demarche, f.scenario, f.titre, f.statut, f.version,
                   f.date_derniere_verification, f.updated_at,
                   coalesce(jsonb_array_length(f.contenu->'points_a_verifier'), 0) AS points_ouverts,
                   (SELECT count(*) FROM feedback fb
                     WHERE fb.fiche_id = f.id AND coalesce(fb.signalement, '') <> '') AS signalements
            FROM fiches f
            WHERE (%(statut)s::text IS NULL OR f.statut = %(statut)s)
              AND (%(langue)s::text IS NULL OR f.langue = %(langue)s)
            ORDER BY f.demarche, f.scenario, f.langue""", {"statut": statut, "langue": langue})
        lignes = [ajouter_fraicheur(dict(r)) for r in cur.fetchall()]
    for l in lignes:
        l["updated_at"] = l["updated_at"].isoformat()
    return lignes


@router.get("/fiches/{slug}/{langue}")
def detail(slug: str, langue: Langue):
    with curseur() as cur:
        ligne = lire_ligne(cur, slug, langue)
        cur.execute("""SELECT action, de, vers, commentaire, par, created_at FROM validations
                       WHERE fiche_id = %s ORDER BY created_at DESC LIMIT 50""", (ligne["id"],))
        journal = [{**dict(r), "created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
        cur.execute("""SELECT utile, signalement, created_at FROM feedback
                       WHERE fiche_id = %s ORDER BY created_at DESC LIMIT 30""", (ligne["id"],))
        retours = [{**dict(r), "created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
    return {
        "slug": slug, "langue": langue, "statut": ligne["statut"], "version": ligne["version"],
        "contenu": ligne["contenu"], "avertissements": avertissements(ligne["contenu"]),
        "transitions": [vers for (de, vers) in TRANSITIONS if de == ligne["statut"]],
        "journal": journal, "retours": retours,
    }


@router.post("/fiches/{slug}/{langue}/statut")
def changer_statut(slug: str, langue: Langue, s: StatutIn, par: str = Depends(admin_connecte)):
    with curseur() as cur:
        ligne = lire_ligne(cur, slug, langue)
        action = TRANSITIONS.get((ligne["statut"], s.statut))
        if action is None:
            raise HTTPException(409, f"Passage « {ligne['statut']} → {s.statut} » non autorisé")
        if action in COMMENTAIRE_OBLIGATOIRE and not (s.commentaire or "").strip():
            raise HTTPException(422, "Un commentaire est obligatoire pour expliquer ce renvoi")

        contenu = {**ligne["contenu"], "statut": s.statut}
        if action == "relecture":  # le relecteur a vérifié la fiche sur la source officielle
            contenu["date_derniere_verification"] = date.today().isoformat()

        cur.execute("""UPDATE fiches SET statut = %s, contenu = %s, date_derniere_verification = %s,
                       updated_at = now() WHERE id = %s""",
                    (s.statut, Json(contenu), contenu["date_derniere_verification"], ligne["id"]))
        journaliser(cur, ligne["id"], action, ligne["statut"], s.statut, s.commentaire, par)
        ecrire_fichier(contenu)
    return {"ok": True, "statut": s.statut, "avertissements": avertissements(contenu)}


@router.put("/fiches/{slug}/{langue}")
def modifier(slug: str, langue: Langue, m: ContenuIn, par: str = Depends(admin_connecte)):
    """Enregistre un nouveau contenu : nouvelle version, retour en brouillon."""
    with curseur() as cur:
        ligne = lire_ligne(cur, slug, langue)
        if m.contenu.get("slug") != slug or m.contenu.get("langue") != langue:
            raise HTTPException(422, "Le slug et la langue ne peuvent pas être modifiés")
        if sans_version({**m.contenu, "statut": ligne["statut"]}) == sans_version(ligne["contenu"]):
            return {"ok": True, "inchangee": True, "version": ligne["version"]}

        version = ligne["version"] + 1
        contenu = {**m.contenu, "version": version, "statut": "brouillon"}
        valider_schema(contenu)
        cur.execute("""UPDATE fiches SET demarche = %s, scenario = %s, titre = %s, contenu = %s,
                       statut = 'brouillon', date_derniere_verification = %s, version = %s,
                       updated_at = now() WHERE id = %s""",
                    (contenu["demarche"], contenu["scenario"], contenu["titre"], Json(contenu),
                     contenu["date_derniere_verification"], version, ligne["id"]))
        cur.execute("INSERT INTO fiche_versions (fiche_id, version, contenu) VALUES (%s, %s, %s)",
                    (ligne["id"], version, Json(contenu)))
        journaliser(cur, ligne["id"], "modification", ligne["statut"], "brouillon",
                    f"version {version}", par)
        ecrire_fichier(contenu)
    return {"ok": True, "version": version, "statut": "brouillon"}


@router.post("/fiches", status_code=201)
def creer(m: ContenuIn, par: str = Depends(admin_connecte)):
    """Nouvelle fiche : toujours créée en brouillon, version 1."""
    contenu = {**m.contenu, "statut": "brouillon", "version": 1}
    valider_schema(contenu)
    if chemin_fichier(contenu["slug"], contenu["langue"]).exists():
        raise HTTPException(409, "Un fichier existe déjà pour ce slug et cette langue")
    with curseur() as cur:
        cur.execute("SELECT 1 FROM fiches WHERE slug = %s AND langue = %s",
                    (contenu["slug"], contenu["langue"]))
        if cur.fetchone():
            raise HTTPException(409, "Cette fiche existe déjà")
        cur.execute("""INSERT INTO fiches (slug, langue, demarche, scenario, titre, contenu,
                       statut, date_derniere_verification, version)
                       VALUES (%s, %s, %s, %s, %s, %s, 'brouillon', %s, 1) RETURNING id""",
                    (contenu["slug"], contenu["langue"], contenu["demarche"], contenu["scenario"],
                     contenu["titre"], Json(contenu), contenu["date_derniere_verification"]))
        fiche_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO fiche_versions (fiche_id, version, contenu) VALUES (%s, 1, %s)",
                    (fiche_id, Json(contenu)))
        journaliser(cur, fiche_id, "creation", None, "brouillon", None, par)
        ecrire_fichier(contenu)
    return {"ok": True, "slug": contenu["slug"], "langue": contenu["langue"]}


@router.delete("/fiches/{slug}/{langue}")
def supprimer(slug: str, langue: Langue):
    """Seul un brouillon peut être supprimé (une fiche publiée se dépublie d'abord)."""
    with curseur() as cur:
        ligne = lire_ligne(cur, slug, langue)
        if ligne["statut"] != "brouillon":
            raise HTTPException(409, "Seul un brouillon peut être supprimé")
        cur.execute("DELETE FROM fiches WHERE id = %s", (ligne["id"],))
    chemin_fichier(slug, langue).unlink(missing_ok=True)
    return {"ok": True}