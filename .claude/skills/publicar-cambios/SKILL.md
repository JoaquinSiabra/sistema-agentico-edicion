---
name: publicar-cambios
description: Cómo subir cambios a este repositorio (sistema-agentico-edicion) según las preferencias de su autor: identidad de git, mensajes de commit, ramas tras una PR fusionada, comprobaciones antes del push, descripción de la PR, CI y revisiones de Codex, y qué decir de las pruebas que no se pueden ejecutar en la nube. Úsala antes del primer commit de la sesión y cuando vayas a hacer commit, push o abrir o actualizar una PR.
---

# Publicar cambios en sistema-agentico-edicion

Lo aprendido en las PRs #9 a #14 (octubre y noviembre de 2026).

## Preferencias del autor sobre la autoría

- Los commits van a su nombre. En las sesiones en la nube la identidad
  de git viene como `Claude <noreply@anthropic.com>`; antes del primer
  commit:
  ```
  git config user.name "Joaquin Siabra"
  git config user.email "joaquin.siabra@gmail.com"
  ```
  y después de cada commit: `git log -1 --format='%h %an <%ae> | %s'`.
- Sin líneas `Co-Authored-By` ni `Claude-Session` en los commits.
- Sin «Generated with Claude Code» ni enlaces a la sesión en la
  descripción de las PRs. La herramienta de GitHub añade ese pie al crear
  la PR; el autor prefiere quitarlo: edita la descripción justo después
  con `update_pull_request` y compruébalo con `pull_request_read` (`get`).
- El autor prefiere que no se publiquen comentarios en GitHub desde la
  sesión. Si un hilo de revisión pide respuesta, cuéntaselo y que decida.

## Ramas

- El autor fusiona las PRs con *squash*. Si la PR anterior de la rama de
  la sesión ya está en `main`, la rama se reinicia desde `main` en vez de
  apilar commits sobre lo ya fusionado:
  ```
  git fetch origin
  git log --oneline -3 origin/main
  git checkout -B <rama-de-la-sesion> origin/main
  ```
- Si la rama remota ya no existe, basta `git push -u origin <rama>`. Si
  existe con commits ya fusionados, subir la rama reiniciada exige un
  push forzado: **pide confirmación al autor antes de hacerlo**, comprueba
  que lo que hay en la rama remota ya está en `main` y usa
  `--force-with-lease` con el commit remoto que has comprobado.
- Para mirar otra versión del repo no uses `git stash` ni
  `git checkout <ref>` (una vez dejó HEAD separado): usa
  `git show origin/main:<fichero>` o `git worktree add` en el scratchpad.

## Commits

- Uno por tarea. Si un fichero mezcla cambios de dos tareas (el README,
  por ejemplo), aplícalos por pasos y haz commit entre medias.
- Mensaje en castellano neutro: asunto corto («Componente: qué cambia») y
  un cuerpo que explique por qué.
- El banco de casos de un agente se fija en su propio commit, antes del
  commit con el código, y después no se ajusta a sus resultados.
  `tests/fijos/` no se modifica sin permiso expreso.

## Antes del push

- Suite rápida con las dos versiones de la CI, 3.11 y 3.13:
  ```
  .venv/bin/python -m pytest -q
  uv venv -p /usr/bin/python3.11 <scratchpad>/venv311
  VIRTUAL_ENV=<scratchpad>/venv311 uv pip install pytest anthropic psycopg2-binary numpy snowballstemmer
  <scratchpad>/venv311/bin/python -m pytest -q -p no:cacheprovider
  ```
- `uvx ruff check --select F,E9` sobre lo tocado.
- Relee el diff buscando lo que haría fallar la CI.

## La PR

- Contra `main`, y **sin fusionarla**: la fusiona el autor.
- Descripción en castellano, con esta estructura:
  - qué incluye (por commits, si hay varios independientes);
  - qué se ha ejecutado y qué no (ver «En la nube», abajo);
  - dudas: lo que la especificación no dejaba claro, qué se hizo y por
    qué. La especificación manda; si un dato tuyo era erróneo y una
    decisión del autor se basó en él, pregúntale antes de cambiar nada.
- Si la rama recibe más commits con la PR abierta, actualiza título y
  descripción.
- Cada spec nueva va en `tests/SPECS.md`, enlazada con su prueba.

## Después de abrir la PR

- **CI** (`.github/workflows/pruebas.yml`): mira la ejecución con
  `actions_list` (`list_workflow_runs`); si falla, corrígela y vuelve a
  subir.
- **Revisiones de Codex.** Codex revisa cada PR al abrirse. Mira los
  hilos con `pull_request_read` (`get_review_comments`):
  - comprueba cada hallazgo con un caso real; si es válido, corrígelo con
    su prueba en un commit propio y marca el hilo como resuelto
    (`resolve_review_thread`);
  - cuéntale al autor qué había y qué hiciste.
- Mira también los hilos abiertos de las PRs anteriores: el del `--tope`
  negativo (#11) se quedó sin atender hasta la #14.

## En la nube

- No hay pgvector, ni acceso a Hugging Face (modelo de embeddings), ni
  `ANTHROPIC_API_KEY`. Las pruebas `--db` y los bancos `--api` se dejan
  escritos y la PR lo dice. PyPI sí funciona.
- Los bancos graban sus respuestas en `.cache/<agente>/respuestas/`; el
  autor las copia a `tests/fijos/<agente>/respuestas/` tras revisarlas.
- El autor no puede abrir rutas del contenedor (tampoco el scratchpad):
  para enseñarle una imagen o un informe, envíaselo con `SendUserFile`.
