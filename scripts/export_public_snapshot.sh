#!/usr/bin/env bash
#
# Exporta un snapshot público curado de este repo (privado) hacia
# ../ai-trading-lab-public, para subir a github.com/<usuario>/ai-trading-lab.
#
# Qué hace:
#   1. Copia solo los archivos versionados en git (`git ls-files`), excluyendo
#      el denylist de abajo (datos/código financiero personal real).
#   2. Genericiza referencias identificables (email EDGAR, nº de cuenta paper).
#   3. Verifica al final que no quede NINGÚN patrón prohibido — si encuentra
#      uno, ABORTA sin tocar el destino más de lo ya copiado. No hace commit
#      ni push por ti: eso siempre es un paso manual y deliberado.
#
# Uso:
#   scripts/export_public_snapshot.sh [ruta_destino]
#   (por defecto: ../ai-trading-lab-public)
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${1:-$SRC_DIR/../ai-trading-lab-public}"

# --- Archivos que NUNCA deben salir de este repo ---
DENYLIST=(
  "data/personal_portfolio.json"
  "data/fiscal_log.json"
  "core/personal_portfolio.py"
  "app/views/portfolio_personal.py"
)

# --- Patrones que, si aparecen en el destino, abortan la exportación ---
FORBIDDEN_PATTERNS=(
  'dvd\.sanchez@gmail'
  'DU1234567'
  'personal_portfolio'
  'portfolio_personal'
  'gitlab\.com/dvd'
)

echo "==> Origen:  $SRC_DIR"
echo "==> Destino: $DEST_DIR"

cd "$SRC_DIR"

if [[ -d "$DEST_DIR/.git" ]]; then
  echo "==> Destino ya es un repo git — se preservará su historial y remoto."
  find "$DEST_DIR" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
else
  rm -rf "$DEST_DIR"
  mkdir -p "$DEST_DIR"
fi

echo "==> Copiando archivos versionados (excepto denylist)..."
while IFS= read -r f; do
  skip=0
  for d in "${DENYLIST[@]}"; do
    if [[ "$f" == "$d" ]]; then
      skip=1
      break
    fi
  done
  [[ "$skip" == 1 ]] && continue
  mkdir -p "$DEST_DIR/$(dirname "$f")"
  cp "$f" "$DEST_DIR/$f"
done < <(git ls-files)

cd "$DEST_DIR"

echo "==> Aplicando parches de genericización..."

# Cuenta paper IBKR → placeholder genérico
if grep -rlq "DU1234567" . 2>/dev/null; then
  grep -rl "DU1234567" . 2>/dev/null | xargs sed -i 's/DU1234567/DU1234567/g'
  echo "    - Cuenta IBKR genericizada."
fi

# Email EDGAR hardcodeado → variable de entorno
if grep -q 'EDGAR_IDENTITY = "dvd.sanchez@gmail.com"' core/config.py 2>/dev/null; then
  sed -i 's|EDGAR_IDENTITY = "dvd.sanchez@gmail.com"|EDGAR_IDENTITY = os.environ.get("EDGAR_IDENTITY", "your-email@example.com")|' core/config.py
  grep -q '^import os$' core/config.py || sed -i '0,/^from pathlib import Path$/s//import os\nfrom pathlib import Path/' core/config.py
  echo "    - Email EDGAR movido a variable de entorno."
fi

# Línea privada de GitLab en requirements.txt
if grep -q '^-e git+ssh://git@gitlab\.com' requirements.txt 2>/dev/null; then
  sed -i '/^-e git+ssh:\/\/git@gitlab\.com/d' requirements.txt
  echo "    - Referencia privada de GitLab eliminada de requirements.txt."
fi

# Constante muerta que apunta al archivo excluido
if grep -q 'PERSONAL_PORTFOLIO_PATH' core/config.py 2>/dev/null; then
  sed -i '/^PERSONAL_PORTFOLIO_PATH = /d' core/config.py
  echo "    - PERSONAL_PORTFOLIO_PATH eliminada de core/config.py."
fi

# Nav "Mi Portfolio" + su rama de importación en el Streamlit
if grep -q 'portfolio_personal' app/streamlit_app.py 2>/dev/null; then
  python3 - <<'PY'
p = "app/streamlit_app.py"
s = open(p).read()
s = s.replace('    "👤 Mi Portfolio": "portfolio_personal",\n', '')
s = s.replace(
    'if selected == "👤 Mi Portfolio":\n'
    '    from app.views.portfolio_personal import render\n'
    '    render()\n'
    'elif selected == "🗂️ Dashboard":',
    'if selected == "🗂️ Dashboard":',
)
open(p, "w").write(s)
PY
  echo "    - Nav 'Mi Portfolio' eliminada de app/streamlit_app.py."
fi

# Menciones sueltas en README/CHANGELOG
for f in README.md README.es.md; do
  [[ -f "$f" ]] || continue
  sed -i \
    -e 's/portfolio_sim, personal_portfolio, rag_store,/portfolio_sim, rag_store,/' \
    -e 's/portfolio_sim, portfolio_personal, trailing_stops)/portfolio_sim, trailing_stops)/' \
    "$f"
done
if [[ -f CHANGELOG.md ]]; then
  sed -i \
    -e '/`app\/views\/portfolio_personal\.py`.*portfolio personal con PnL/d' \
    -e '/`core\/personal_portfolio\.py`.*CRUD portfolio personal/d' \
    CHANGELOG.md
fi
echo "    - Menciones sueltas limpiadas en README/CHANGELOG."

# Sección "Repo público" de CLAUDE.md: documenta el propio proceso de export
# para quien mantiene el repo PRIVADO (denylist, patrones, nombres de
# módulos excluidos) — no aporta nada al lector del mirror público y sus
# referencias en prosa a módulos denylisted disparan la verificación de
# abajo en falso. Se elimina desde su encabezado hasta el siguiente "## ".
if [[ -f CLAUDE.md ]]; then
  python3 - <<'PY'
import re
p = "CLAUDE.md"
s = open(p).read()
s = re.sub(r"\n## Repo público.*?(?=\n## )", "", s, flags=re.DOTALL)
open(p, "w").write(s)
PY
  echo "    - Sección 'Repo público' eliminada de CLAUDE.md."
fi

echo "==> Verificación final (bloqueante)..."
found=0
for pat in "${FORBIDDEN_PATTERNS[@]}"; do
  if grep -rniE "$pat" . --exclude-dir=.git --exclude=export_public_snapshot.sh 2>/dev/null | grep -q .; then
    echo
    echo "❌ Patrón prohibido encontrado: $pat"
    grep -rniE "$pat" . --exclude-dir=.git --exclude=export_public_snapshot.sh 2>/dev/null
    found=1
  fi
done

if [[ "$found" == "1" ]]; then
  echo
  echo "==> ABORTADO. El snapshot en $DEST_DIR quedó con contenido sensible."
  echo "    Revisa manualmente (probablemente el repo privado cambió algo que"
  echo "    este script no conocía — el import de un módulo excluido, una nueva"
  echo "    referencia al email/cuenta, etc.) y vuelve a correr el script."
  exit 1
fi

echo
echo "✅ Snapshot limpio en $DEST_DIR."
echo "   Revisa 'git -C $DEST_DIR status' / 'git -C $DEST_DIR diff' y haz"
echo "   commit + push manualmente cuando estés a gusto. Este script nunca"
echo "   commitea ni pushea por ti."
