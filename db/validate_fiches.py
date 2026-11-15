"""Valide toutes les fiches JSON du dossier fiches/ contre schema.json.
Usage: pip install jsonschema && python validate_fiches.py
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

racine = Path(__file__).parent / "fiches"
schema = json.loads((racine / "schema.json").read_text(encoding="utf-8"))
validator = Draft202012Validator(schema)

erreurs = 0
for fichier in sorted(racine.glob("*.json")):
    if fichier.name == "schema.json":
        continue
    fiche = json.loads(fichier.read_text(encoding="utf-8"))
    problemes = list(validator.iter_errors(fiche))
    if problemes:
        erreurs += 1
        print(f"{fichier.name}")
        for p in problemes:
            print(f"   - {list(p.path)}: {p.message}")
    else:
        print(f"{fichier.name}")

sys.exit(1 if erreurs else 0)
