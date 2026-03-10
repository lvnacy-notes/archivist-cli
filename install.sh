#!/bin/sh
#
# Archivist installer
# Usage: curl -fsSL https://raw.githubusercontent.com/lvnacy-notes/archivist-cli/main/install.sh | bash
#

set -e

REPO_URL="https://github.com/your-handle/archivist-cli.git"
INSTALL_DIR="$HOME/tools/archivist-cli"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo "  ✅ $1"; }
warn()  { echo "  ⚠️  $1"; }
error() { echo "  ❌ $1"; exit 1; }
step()  { echo ""; echo "── $1"; }

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

step "Checking requirements"

# git
if ! command -v git >/dev/null 2>&1; then
    error "git is required but not found. Install git and try again."
fi
info "git found: $(git --version)"

# Python 3.10+
PYTHON=""
if command -v pyenv >/dev/null 2>&1; then
    PYTHON="$(pyenv which python 2>/dev/null || true)"
fi
if [ -z "$PYTHON" ]; then
    PYTHON="$(command -v python3 || command -v python || true)"
fi
if [ -z "$PYTHON" ]; then
    error "Python 3.10+ is required but not found."
fi

PYTHON_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    error "Python 3.10+ required. Found $PYTHON_VERSION at $PYTHON."
fi
info "Python $PYTHON_VERSION found: $PYTHON"

# pip
PIP=""
if command -v pyenv >/dev/null 2>&1; then
    PIP="$(pyenv which pip 2>/dev/null || true)"
fi
if [ -z "$PIP" ]; then
    PIP="$(command -v pip3 || command -v pip || true)"
fi
if [ -z "$PIP" ]; then
    error "pip is required but not found."
fi
info "pip found: $PIP"

# ---------------------------------------------------------------------------
# Clone or update
# ---------------------------------------------------------------------------

step "Installing archivist-cli"

if [ -d "$INSTALL_DIR/.git" ]; then
    warn "Found existing install at $INSTALL_DIR — pulling latest."
    git -C "$INSTALL_DIR" pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        error "$INSTALL_DIR already exists but is not a git repo. Move or remove it and try again."
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
    info "Cloned to $INSTALL_DIR"
fi

# ---------------------------------------------------------------------------
# Install package
# ---------------------------------------------------------------------------

step "Installing Python package"

"$PIP" install -e "$INSTALL_DIR" --quiet
info "archivist installed (editable)"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

step "Verifying install"

if ! command -v archivist >/dev/null 2>&1; then
    warn "archivist not found on PATH after install."
    warn "You may need to add pip's bin directory to your PATH:"
    warn "  $("$PIP" show -f archivist 2>/dev/null | grep Location | awk '{print $2}')/../bin"
    warn "Then run: archivist hooks install"
    exit 1
fi

ARCHIVIST_VERSION=$(archivist --version 2>/dev/null || echo "installed")
info "archivist ready: $ARCHIVIST_VERSION"

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

step "Installing git hooks"

archivist hooks install
info "Global hooks installed — all future clones will include them automatically."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🗃️  Archivist installed successfully!"
echo ""
echo "  Next steps:"
echo "    cd your-project"
echo "    archivist init"
echo ""
echo "  For existing repos already cloned:"
echo "    archivist hooks sync"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""