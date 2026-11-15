"""Veille DémarcheClair: détecte quand une source officielle citée dans les fiches a changé.

Usage, depuis la racine du projet:
    python veille/verifier_sources.py        # vérifie toutes les sources
    python veille/verifier_sources.py --ok   # après relecture des fiches: efface les alertes
"""
import hashlib
import json
import sys
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
FICHES = DOSSIER.parent / "fiches"
ETAT = DOSSIER / "etat.json"
RAPPORT = DOSSIER / "rapport.txt"


class Texte(HTMLParser):
    """Extrait le texte visible d'une page (sans scripts ni styles)."""
    def __init__(self):
        super().__init__()
        self.morceaux, self.ignore = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.ignore += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.ignore:
            self.ignore -= 1

    def handle_data(self, data):
        if not self.ignore:
            self.morceaux.append(data)


def empreinte(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DemarcheClair-veille/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        brut = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    p = Texte()
    p.feed(brut)
    texte = " ".join(" ".join(p.morceaux).split())
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def trouver_sources(obj):
    """Cherche récursivement les listes 'sources' ([{nom, url}]) dans une fiche."""
    if isinstance(obj, dict):
        for cle, val in obj.items():
            if cle == "sources" and isinstance(val, list):
                for s in val:
                    if isinstance(s, dict) and str(s.get("url", "")).startswith("http"):
                        yield s["url"]
            else:
                yield from trouver_sources(val)
    elif isinstance(obj, list):
        for x in obj:
            yield from trouver_sources(x)


def main():
    etat = json.loads(ETAT.read_text(encoding="utf-8")) if ETAT.exists() else {}

    if "--ok" in sys.argv:
        for e in etat.values():
            e["a_revoir"] = False
        ETAT.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Alertes effacées.")
        return

    urls = {}  # url -> fiches qui la citent
    for f in sorted(FICHES.glob("*.json")):
        if f.name == "schema.json":
            continue
        for u in set(trouver_sources(json.loads(f.read_text(encoding="utf-8")))):
            urls.setdefault(u, []).append(f.name)

    aujourdhui = date.today().isoformat()
    lignes, erreurs = [], 0
    for url, fiches in urls.items():
        e = etat.setdefault(url, {})
        try:
            h = empreinte(url)
        except Exception as ex:
            erreurs += 1
            lignes.append(f"[ERREUR]  {url}\n          {type(ex).__name__}: {ex}")
            continue
        if "hash" not in e:
            e.update(hash=h, a_revoir=False, derniere_verif=aujourdhui)
            lignes.append(f"[NOUVELLE] {url} (référence enregistrée)")
        elif e["hash"] != h:
            e.update(hash=h, a_revoir=True, date_changement=aujourdhui, derniere_verif=aujourdhui)
        else:
            e["derniere_verif"] = aujourdhui
        if e.get("a_revoir"):
            lignes.append(f"[A REVOIR] {url}\n          changée le {e.get('date_changement')}, citée par: {', '.join(fiches)}")

    ETAT.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")
    rapport = "\n".join(lignes) or "Rien à signaler: aucune source n'a changé."
    RAPPORT.write_text(f"Veille du {aujourdhui}\n\n{rapport}\n", encoding="utf-8")
    print(rapport)
    sys.exit(1 if any("[A REVOIR]" in l for l in lignes) else 0)


if __name__ == "__main__":
    main()