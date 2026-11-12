CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fiches (
    id                         SERIAL PRIMARY KEY,
    slug                       TEXT NOT NULL,
    langue                     TEXT NOT NULL CHECK (langue IN ('fr', 'ar')),
    demarche                   TEXT NOT NULL,
    scenario                   TEXT NOT NULL,
    titre                      TEXT NOT NULL,
    contenu                    JSONB NOT NULL,          -- la fiche complète (voir fiches/schema.json)
    statut                     TEXT NOT NULL DEFAULT 'brouillon' CHECK (statut IN ('brouillon', 'relu', 'publie')),
    date_derniere_verification DATE,
    version                    INTEGER NOT NULL DEFAULT 1,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (slug, langue)
);

CREATE TABLE IF NOT EXISTS fiche_versions (
    id         SERIAL PRIMARY KEY,
    fiche_id   INTEGER NOT NULL REFERENCES fiches(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    contenu    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    id          SERIAL PRIMARY KEY,
    fiche_id    INTEGER REFERENCES fiches(id) ON DELETE SET NULL,
    utile       BOOLEAN,
    signalement TEXT,                                   -- aucune donnée personnelle
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chunks pour le RAG (la dimension dépendra du modèle d'embedding choisi)
CREATE TABLE IF NOT EXISTS fiche_chunks (
    id        SERIAL PRIMARY KEY,
    fiche_id  INTEGER NOT NULL REFERENCES fiches(id) ON DELETE CASCADE,
    texte     TEXT NOT NULL,
    embedding vector(384)
);
