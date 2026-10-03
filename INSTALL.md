# Instalación

Esta guía cubre dos cosas: el entorno Python para el pipeline de extracción (Fases 0a, 0b y 1, ya documentado en el [README](README.md)), y la puesta en marcha del **Almacén** (Fase 2: Postgres + pgvector), que guarda la prosa vectorizada y las fichas canónicas para que el futuro retriever híbrido pueda consultarlas.

El Almacén corre sobre PostgreSQL + pgvector. En Windows, la vía recomendada es WSL2, porque el repositorio `universe` de Ubuntu no empaqueta pgvector de forma fiable.

## 1. Requisitos previos

- Python 3.11+ y [`uv`](https://github.com/astral-sh/uv).
- Una `ANTHROPIC_API_KEY` en el entorno (la usan las Fases 0a, 0b y 1).
- Si vas a usar el Almacén: Windows con WSL2, o cualquier Linux/macOS con PostgreSQL 16+ instalable directamente.

## 2. Clonar el repositorio e instalar dependencias

```bash
git clone https://github.com/JoaquinSiabra/sistema-agentico-edicion.git
cd sistema-agentico-edicion
uv pip install -r requirements.txt
```

Con esto ya puedes correr las Fases 0a, 0b y 1 como describe el README. Lo que sigue es solo para la Fase 2 (Almacén).

## 3. Instalar WSL2 + Ubuntu (solo Windows)

Si no lo tienes ya, en PowerShell **como administrador**:

```powershell
wsl --install -d Ubuntu
```

Reinicia si lo pide y crea el usuario de Ubuntu la primera vez que abras la terminal.

## 4. Instalar PostgreSQL + pgvector

Dentro de la terminal de Ubuntu (WSL), o en tu distribución Linux habitual:

```bash
sudo apt update && sudo apt install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update

sudo apt install -y postgresql-16 postgresql-16-pgvector
sudo service postgresql start
```

## 5. Crear rol, base de datos y extensión

Los nombres deben coincidir con el DSN por defecto que usa `src/poblar_almacen.py` (`dbname=almacen user=editor password=editor_dev host=localhost port=5432`). El rol se crea como superusuario solo por comodidad de desarrollo local, no para producción:

```bash
sudo -u postgres psql -c "CREATE ROLE editor WITH LOGIN PASSWORD 'editor_dev' SUPERUSER;"
sudo -u postgres psql -c "CREATE DATABASE almacen OWNER editor;"
```

Si usas credenciales distintas, pásalas con el argumento `--dsn` que acepta el script, en vez de tocar el código.

## 6. Aplicar el esquema

Desde la raíz del repo:

```bash
psql -U editor -d almacen -h localhost -f schema.sql
```

Esto crea la extensión `vector`, las tablas del Almacén (`obras`, `entidades`, `fragmentos`, `pasajes`, `hechos_canonicos`, y sus relaciones) y el índice HNSW para búsqueda semántica sobre `pasajes.embedding` (cada fragmento se trocea en pasajes de ~400 tokens, porque el modelo de embeddings trunca a 512).

## 7. Arranque automático de Postgres (WSL2)

WSL2 no mantiene servicios vivos entre sesiones salvo que tengas `systemd` activo. Comprueba `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Si lo agregaste recién, reinicia WSL (`wsl --shutdown` desde PowerShell y vuelve a abrir la terminal) y confirma que el servicio arranque solo (`systemctl is-active postgresql`). Si prefieres no tocarlo, ejecuta `sudo service postgresql start` cada vez que abras WSL antes de usar el Almacén.

Si acabas de instalar todo y no logras conectar a `localhost:5432` desde Windows aunque Postgres esté activo dentro de WSL, prueba `wsl --shutdown` y vuelve a abrir la distro: el reenvío de `localhost` de WSL2 a veces no queda activo hasta el primer reinicio completo de la VM.

## 8. Confirmar acceso desde Windows

```powershell
Test-NetConnection -ComputerName localhost -Port 5432
```

Debe devolver `TcpTestSucceeded : True`. Con el reenvío de `localhost` de WSL2 activo, un script Python corriendo en Windows se conecta igual que uno corriendo dentro de WSL, sin tocar el DSN.

## 9. Poblar el Almacén

`src/poblar_almacen.py` usa un modelo de embeddings local (`sentence-transformers`, `intfloat/multilingual-e5-large`, 1024 dimensiones) en vez de una API de pago, y necesita además el cliente de PostgreSQL. Ninguno de los dos está en `requirements.txt` todavía:

```bash
uv pip install psycopg2-binary sentence-transformers
```

Luego, para una obra ya procesada por las Fases 0 y 1:

```bash
uv run python src/poblar_almacen.py --obra=<nombre_obra> --exports=data/<nombre_obra>/exports
```

El script asume que `extraer_canon.py` escribe un fichero JSON por fragmento bajo `data/<obra>/exports/canon/{orden:04d}.json` con las claves `orden_fragmento` y `ruta_fragmento_origen` — ver el docstring de `poblar_almacen.py` para el detalle de ese prerrequisito.
