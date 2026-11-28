"""Change le statut de fiches (brouillon -> relu -> publie).

Usage (depuis la racine du projet):
    python changer_statut.py relu cnie-renouvellement cnie-faq
    python changer_statut.py publie tous
"""
import json
import sys
from pathlib import Path

STATUTS = ("brouillon", "relu", "publie")
dossier = Path(__file__).parent / "fiches"

if len(sys.argv) < 3 or sys.argv[1] not in STATUTS:
    sys.exit("Usage: python changer_statut.py <brouillon|relu|publie> <slug ... | tous>")
statut, slugs = sys.argv[1], sys.argv[2:]

modifies, trouves = 0, set()
for fichier in sorted(dossier.glob("*.json")):
    if fichier.name == "schema.json":
        continue
    fiche = json.loads(fichier.read_text(encoding="utf-8"))
    if slugs != ["tous"] and fiche["slug"] not in slugs:
        continues
    trouves.add(fiche["slug"])
    if fiche["statut"] != statut:
        fiche["statut"] = statut
        fichier.write_text(json.dumps(fiche, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        modifies += 1
    print(f"{fichier.name}: {statut}")

for s in slugs:
    if s != "tous" and s not in trouves:
        print(f"slug inconnu: {s}")
print(f"{modifies} fichier(s) modifié(s). Ensuite: python validate_fiches.py puis python db/charger_fiches.py")