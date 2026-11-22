"""Crée un compte administrateur, ou change son mot de passe s'il existe déjà.

Usage, depuis la racine du projet (la base Docker doit tourner) :
    python -m backend.creer_admin
"""
import getpass
import sys

from backend.main import curseur  # en premier : main.py charge ensuite admin.py
from backend.admin import creer_ou_modifier_admin, creer_tables


def main() -> int:
    identifiant = input("Identifiant : ").strip()
    if not identifiant:
        print("Identifiant vide, rien n'est fait.")
        return 1
    mdp = getpass.getpass("Mot de passe (8 caractères minimum, invisible quand tu tapes) : ")
    if len(mdp) < 8:
        print("Mot de passe trop court.")
        return 1
    if getpass.getpass("Confirme le mot de passe : ") != mdp:
        print("Les deux mots de passe sont différents.")
        return 1
    with curseur() as cur:
        creer_tables(cur)
        print(f"Compte « {identifiant} » : {creer_ou_modifier_admin(cur, identifiant, mdp)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())