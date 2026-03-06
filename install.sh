#!/usr/bin/env bash
# MKHA Installer — MikroTik High Availability Manager
# Usage: curl -fsSL https://raw.githubusercontent.com/grandir66/MKHA/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/grandir66/MKHA.git"
INSTALL_DIR="${MKHA_INSTALL_DIR:-$HOME/mkha}"
PYTHON="${MKHA_PYTHON:-python3}"
BRANCH="${MKHA_BRANCH:-main}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[MKHA]${NC} $*"; }
ok()    { echo -e "${GREEN}[MKHA]${NC} $*"; }
warn()  { echo -e "${YELLOW}[MKHA]${NC} $*"; }
fail()  { echo -e "${RED}[MKHA]${NC} $*"; exit 1; }

# --- Pre-checks -----------------------------------------------------------

command -v git >/dev/null 2>&1 || fail "git is required but not installed."
command -v "$PYTHON" >/dev/null 2>&1 || fail "$PYTHON is required but not installed."

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    fail "Python 3.11+ is required (found $PY_VERSION)."
fi

info "Python $PY_VERSION detected"

# --- Clone or update -------------------------------------------------------

if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
    info "Cloning MKHA into $INSTALL_DIR..."
    git clone --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- Virtual environment ---------------------------------------------------

if [ ! -d ".venv" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv .venv
fi

info "Installing dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# --- Config ----------------------------------------------------------------

if [ ! -f "config/ha_config.yaml" ]; then
    info "Creating config from template..."
    cp config/ha_config.yaml.example config/ha_config.yaml
    warn "Edit config/ha_config.yaml with your router IPs and credentials."
else
    ok "Config file already exists, skipping."
fi

# --- Helper scripts --------------------------------------------------------

MKHA_BIN="$INSTALL_DIR/.venv/bin/mkha-run"
cat > "$MKHA_BIN" << 'SCRIPT'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$DIR/.venv/bin/python" -m src.main -c "$DIR/config/ha_config.yaml" "$@"
SCRIPT
chmod +x "$MKHA_BIN"

# --- Summary ---------------------------------------------------------------

echo ""
ok "============================================"
ok "  MKHA installed successfully!"
ok "============================================"
echo ""
info "Location:  $INSTALL_DIR"
info "Config:    $INSTALL_DIR/config/ha_config.yaml"
info "Version:   $(.venv/bin/python -c 'from src.version import __version__; print(__version__)')"
echo ""
info "To start MKHA:"
echo ""
echo "  cd $INSTALL_DIR"
echo "  .venv/bin/python -m src.main -c config/ha_config.yaml"
echo ""
info "Or use the helper script:"
echo ""
echo "  $MKHA_BIN"
echo ""
info "Web dashboard: http://localhost:8080"
echo ""
