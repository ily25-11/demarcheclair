"""Améliore la page Qualité: elle montre maintenant ce qu'il reste à traiter.

Usage (depuis la racine du projet):  python patch_stats.py
Modifie backend/main.py et frontend/index.html (copies de sauvegarde en .bak).
"""
import re
import shutil
from pathlib import Path

RACINE = Path(__file__).parent
MAIN, INDEX = RACINE / "backend" / "main.py", RACINE / "frontend" / "index.html"


def remplacer_une_fois(s, ancien, nouveau, nom):
    assert s.count(ancien) == 1, f"Point d'ancrage introuvable ou en double: {nom}"
    return s.replace(ancien, nouveau)


def sauver(chemin, texte):
    shutil.copy(chemin, chemin.with_name(chemin.name + ".bak"))
    chemin.write_text(texte, encoding="utf-8")


# ---------------- backend/main.py ----------------
STATS_PY = '''@app.get("/stats")
def stats():
    """Tableau de bord qualité: ce qu'il reste à traiter."""
    with curseur() as cur:
        cur.execute("""SELECT f.slug, f.langue, f.titre, f.statut, f.date_derniere_verification,
                   coalesce(jsonb_array_length(f.contenu->'points_a_verifier'), 0) AS points_ouverts,
                   count(fb.id) FILTER (WHERE fb.utile) AS utile,
                   count(fb.id) FILTER (WHERE NOT fb.utile) AS pas_utile
                   FROM fiches f LEFT JOIN feedback fb ON fb.fiche_id = f.id
                   GROUP BY f.id ORDER BY f.slug, f.langue""")
        fiches = [ajouter_fraicheur(dict(r)) for r in cur.fetchall()]
        cur.execute("""SELECT f.slug, f.titre, fb.signalement AS texte
                   FROM feedback fb JOIN fiches f ON f.id = fb.fiche_id
                   WHERE coalesce(fb.signalement, '') <> ''
                   ORDER BY fb.created_at DESC LIMIT 10""")
        signalements = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT q, count(*) AS fois FROM recherches WHERE nb_resultats = 0
                   GROUP BY q ORDER BY fois DESC LIMIT 10""")
        vides = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS total, count(*) FILTER (WHERE hors_base) AS hors_base FROM chat_log")
        chat = dict(cur.fetchone())
        cur.execute("""SELECT question, count(*) AS fois FROM chat_log
                   WHERE hors_base AND question IS NOT NULL GROUP BY question
                   ORDER BY fois DESC, max(created_at) DESC LIMIT 10""")
        chat["questions"] = [dict(r) for r in cur.fetchall()]
    return {"fiches": fiches, "signalements": signalements,
            "recherches": {"sans_resultat": vides}, "chat": chat}
'''

main = MAIN.read_text(encoding="utf-8")
if "chat_log" in main:
    print("backend/main.py: déjà à jour")
else:
    main = remplacer_une_fois(main,
        'nb_resultats INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")',
        'nb_resultats INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")\n'
        '        # Journal du chatbot: le texte n\'est gardé que pour les questions restées sans réponse\n'
        '        cur.execute("""CREATE TABLE IF NOT EXISTS chat_log (\n'
        '            id SERIAL PRIMARY KEY, langue TEXT, hors_base BOOLEAN NOT NULL,\n'
        '            question TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")', "table chat_log")
    main = remplacer_une_fois(main,
        '        return repondre(c.question, fiches, [m.model_dump() for m in c.historique])',
        '        res = repondre(c.question, fiches, [m.model_dump() for m in c.historique])\n'
        '        try:\n'
        '            with curseur() as cur:\n'
        '                cur.execute("INSERT INTO chat_log (langue, hors_base, question) VALUES (%s, %s, %s)",\n'
        '                            (c.langue, res["hors_base"], c.question if res["hors_base"] else None))\n'
        '        except Exception:\n'
        '            traceback.print_exc()  # le journal ne doit jamais empêcher de répondre\n'
        '        return res', "journal du chat")
    main, n = re.subn(r'@app\.get\("/stats"\)\ndef stats\(\):.*?(?=\n\nclass MessageIn)', lambda m: STATS_PY.rstrip("\n"), main, flags=re.S)
    assert n == 1, "Fonction /stats introuvable"
    sauver(MAIN, main)
    print("backend/main.py: mis à jour")

# ---------------- frontend/index.html ----------------
STATS_JS = '''async function renderStats() {
  try {
    const s = await api("/stats");
    const fiches = s.fiches;
    const prets = fiches.filter(f => f.statut !== "brouillon").length;
    const u = fiches.reduce((a, f) => a + f.utile, 0), pu = fiches.reduce((a, f) => a + f.pas_utile, 0);
    const pct = (a, b) => b ? Math.round(a / b * 100) + " %" : "—";
    const kpi = (v, l) => h("div", {}, h("h3", { class: "big" }, v), h("p", {}, l));
    const lien = f => h("a", { href: `#/fiche/${f.slug}` }, h("bdi", {}, f.titre));
    const fois = n => n > 1 ? ` × ${n}` : "";
    const liste = (titre, items) => items.length ? h("div", { class: "block" }, h("h2", {}, titre), h("ul", {}, items)) : null;
    const aCompleter = fiches.filter(f => f.points_ouverts > 0 || f.statut === "brouillon" || (f.jours_depuis_verification ?? 999) > 90);
    const blocs = [
      liste(t().tReports, s.signalements.map(r => h("li", {}, h("bdi", {}, `« ${r.texte} »`), " — ", lien(r)))),
      liste(t().tChat, s.chat.questions.map(q => h("li", {}, h("bdi", {}, q.question), fois(q.fois)))),
      liste(t().tSearch, s.recherches.sans_resultat.map(r => h("li", {}, h("bdi", {}, `« ${r.q} »`), fois(r.fois)))),
      liste(t().tFiches, aCompleter.map(f => h("li", {}, lien(f), " ",
        h("span", { class: "chip warn" }, f.points_ouverts ? t().points(f.points_ouverts) : t().draft), " ",
        h("span", { class: "chip" }, f.langue.toUpperCase()))))
    ].filter(Boolean);
    app.replaceChildren(h("div", { class: "wrap" }, backLink(), h("h1", {}, t().stats),
      h("div", { class: "block facts" }, kpi(`${prets} / ${fiches.length}`, t().kReady),
        kpi(pct(u, u + pu), t().kUseful), kpi(pct(s.chat.hors_base, s.chat.total), t().kUnanswered)),
      h("h2", {}, t().todo), ...(blocs.length ? blocs : [h("p", {}, t().allGood)])));
  } catch { app.replaceChildren(h("div", { class: "wrap" }, backLink(), h("p", { class: "msg" }, t().apiDown))); }
}
'''
FR = '''sNoneYet: "Rien pour l’instant.",
    kReady: "Fiches relues ou publiées", kUseful: "Retours « utile »", kUnanswered: "Questions sans réponse (assistant)",
    todo: "À traiter", tReports: "Signalements des utilisateurs", tChat: "Questions sans réponse posées à l’assistant",
    tSearch: "Recherches sans résultat", tFiches: "Fiches à compléter",
    points: n => `${n} point${n > 1 ? "s" : ""} à vérifier`, allGood: "Tout est à jour."'''
AR = '''sNoneYet: "لا شيء حاليا.",
    kReady: "الصفحات المراجعة أو المنشورة", kUseful: "ملاحظات «مفيدة»", kUnanswered: "أسئلة بلا جواب (المساعد)",
    todo: "للمعالجة", tReports: "ملاحظات المستخدمين", tChat: "أسئلة بلا جواب طُرحت على المساعد",
    tSearch: "عمليات بحث بدون نتائج", tFiches: "صفحات للإكمال",
    points: n => `${n} نقاط للتحقق`, allGood: "كل شيء محدّث."'''

index = INDEX.read_text(encoding="utf-8")
if "kReady" in index:
    print("frontend/index.html: déjà à jour")
else:
    index = remplacer_une_fois(index, 'sNoneYet: "Rien pour l’instant."', FR, "textes français")
    index = remplacer_une_fois(index, 'sNoneYet: "لا شيء حاليا."', AR, "textes arabes")
    index, n = re.subn(r'async function renderStats\(\) \{.*?(?=\n/\* ----------)', lambda m: STATS_JS.rstrip("\n") + "\n", index, flags=re.S)
    assert n == 1, "Fonction renderStats introuvable"
    sauver(INDEX, index)
    print("frontend/index.html: mis à jour")
print("Terminé. Redémarre l'API si besoin, puis Ctrl+F5 sur la page.")