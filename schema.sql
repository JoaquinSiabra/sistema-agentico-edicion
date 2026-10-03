-- schema.sql
--
-- Doble representación del Almacén:
--   (a) fragmentos       -> PROSA VECTORIZADA (recuperación semántica)
--   (b) hechos_canonicos -> FICHAS CANÓNICAS (filtrado estructurado anti-spoiler)
--
-- Ambas comparten la misma clave de orden de lectura (orden_fragmento +
-- orden_en_fragmento -> posicion_global) para que el retriever híbrido
-- pueda filtrar "todo lo canónico hasta aquí" con un WHERE sobre un
-- entero, sin tener que re-derivar el orden narrativo en cada consulta.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE obras (
    id      SERIAL PRIMARY KEY,
    slug    TEXT UNIQUE NOT NULL,   -- coincide con data/<obra>/
    titulo  TEXT
);

-- Registro unificado de personajes/lugares/organizaciones/glosario
-- (Fase 0b calibrada). Es el mismo concepto de "entidad" que ya usan
-- calibrar_glosario.py y extraer_canon.py, solo que aquí vive en tabla
-- relacional en vez de en JSON, para poder hacer FK desde hechos y
-- fragmentos.
CREATE TABLE entidades (
    id          SERIAL PRIMARY KEY,
    obra_id     INT NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    termino     TEXT NOT NULL,           -- forma canónica
    categoria   TEXT NOT NULL,           -- persona | lugar | organizacion | objeto | concepto...
    alias       TEXT[] NOT NULL DEFAULT '{}',
    variantes   TEXT[] NOT NULL DEFAULT '{}',
    confianza   TEXT,
    UNIQUE (obra_id, termino)
);

-- (a) PROSA VECTORIZADA
CREATE TABLE fragmentos (
    id               SERIAL PRIMARY KEY,
    obra_id          INT NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    orden_fragmento  INT NOT NULL,        -- posición secuencial de lectura en la obra
    tipo_documental  TEXT,                -- de la taxonomía calibrada en Fase 0a
    texto            TEXT NOT NULL,
    embedding        VECTOR(1024) NOT NULL,  -- dim de multilingual-e5-large; si cambias de
                                              -- modelo de embeddings, esta columna y el
                                              -- índice hnsw hay que recrearlos
    ruta_origen      TEXT,                -- trazabilidad al .txt original
    UNIQUE (obra_id, orden_fragmento)
);

CREATE INDEX fragmentos_embedding_idx ON fragmentos
    USING hnsw (embedding vector_cosine_ops);

-- (b) FICHAS CANÓNICAS
CREATE TABLE hechos_canonicos (
    id                       SERIAL PRIMARY KEY,
    obra_id                  INT NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    fragmento_id             INT NOT NULL REFERENCES fragmentos(id) ON DELETE CASCADE,
    orden_en_fragmento       INT NOT NULL,  -- índice del hecho dentro del array que
                                            -- devuelve extraer_canon.py (aproxima el
                                            -- orden intra-fragmento; el modelo no
                                            -- garantiza que sea exacto)
    posicion_global          INT NOT NULL,  -- orden_fragmento * 10000 + orden_en_fragmento
                                            -- (asume <10000 hechos por fragmento)
    hecho                    TEXT NOT NULL,
    momento_canonico         TEXT NOT NULL, -- referencia textual libre del propio modelo
                                            -- (trazabilidad/cita humana); NO es ordenable
                                            -- por sí sola, por eso existe posicion_global
    atribucion_tipo          TEXT NOT NULL
        CHECK (atribucion_tipo IN ('narrador_documental', 'afirmacion_personaje')),
    atribucion_personaje_id  INT REFERENCES entidades(id)  -- null si narrador_documental
);

CREATE INDEX hechos_posicion_idx ON hechos_canonicos (obra_id, posicion_global);

-- Menciones: coincidencia por término canónico exacto (heurística v1,
-- no resuelve variantes que el glosario no haya registrado todavía).
CREATE TABLE fragmento_entidad (
    fragmento_id  INT NOT NULL REFERENCES fragmentos(id) ON DELETE CASCADE,
    entidad_id    INT NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    PRIMARY KEY (fragmento_id, entidad_id)
);

-- Existe pero NO se puebla todavía: extraer_canon.py no devuelve qué
-- entidades intervienen en cada hecho, solo quién lo afirma
-- (atribucion.personaje). Poblarla requiere o bien ampliar ese esquema
-- de salida, o bien un matching de texto libre sobre "hecho" — pendiente,
-- no especular la solución hasta tener el caso de uso del retriever.
CREATE TABLE hecho_entidad (
    hecho_id    INT NOT NULL REFERENCES hechos_canonicos(id) ON DELETE CASCADE,
    entidad_id  INT NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    PRIMARY KEY (hecho_id, entidad_id)
);
