#!/bin/bash
# ============================================================================
# Hermes Agent — Cognitive Substrate Edition: installer
# ============================================================================
# This fork (ggrace519/hermes-agent) layers a PostgreSQL-backed cognitive
# substrate (Phase A skeleton + Phase B Curator + Phase C recall with
# pgvector embeddings) on top of the upstream NousResearch/hermes-agent
# project. It REPLACES SQLite with PostgreSQL for all state — session
# transcripts, kanban, substrate slices.
#
# Designed to install side-by-side with the upstream Hermes Agent
# without touching its data. Defaults:
#
#   - INSTALL_DIR:  ~/.hermes-substrate/hermes-agent  (separate from upstream)
#   - HERMES_HOME:  ~/.hermes-substrate               (separate from upstream)
#   - CLI command:  hermes-substrate                  (no collision with `hermes`)
#   - PostgreSQL:   docker compose service on port 5432, db `hermes`
#
# All four defaults can be overridden. If you do NOT have an upstream
# Hermes install on the machine and want the natural `hermes` CLI name +
# `~/.hermes/` home, pass:  --cli-name hermes --hermes-home ~/.hermes
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ggrace519/hermes-agent/main/scripts/install.sh | bash
#
# Or with options:
#   curl -fsSL ... | bash -s -- --skip-postgres --skip-setup
#
# ============================================================================

set -e

# ── Environment guards ──────────────────────────────────────────────────────
# A pre-set PYTHONPATH can force pip/entrypoints to import a different
# checkout than the one being installed, which makes fresh installs appear
# broken or stale. Same idea as upstream — preserved here.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONHOME during install"
    unset PYTHONHOME
fi
# Prevent uv from discovering config files (uv.toml, pyproject.toml) from the
# wrong user's home when running under sudo -u <user>.
export UV_NO_CONFIG=1

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# ── Configuration ───────────────────────────────────────────────────────────
REPO_URL_SSH="git@github.com:ggrace519/hermes-agent.git"
REPO_URL_HTTPS="https://github.com/ggrace519/hermes-agent.git"

# Defaults are SIDE-BY-SIDE safe. See header for rationale.
HERMES_HOME_DEFAULT="$HOME/.hermes-substrate"
CLI_NAME_DEFAULT="hermes-substrate"

HERMES_HOME="${HERMES_HOME:-$HERMES_HOME_DEFAULT}"
CLI_NAME="${HERMES_CLI_NAME:-$CLI_NAME_DEFAULT}"

# INSTALL_DIR resolved after arg parsing + OS detection.
if [ -n "${HERMES_INSTALL_DIR:-}" ]; then
    INSTALL_DIR="$HERMES_INSTALL_DIR"
    INSTALL_DIR_EXPLICIT=true
else
    INSTALL_DIR=""
    INSTALL_DIR_EXPLICIT=false
fi

PYTHON_VERSION="3.11"
NODE_VERSION="22"

# PostgreSQL — substrate's source of truth.
# Defaults match the docker-compose.yml shipped with this repo.
PG_HOST_DEFAULT="localhost"
PG_PORT_DEFAULT="5432"
PG_USER_DEFAULT="hermes"
PG_PASSWORD_DEFAULT="hermes"
PG_DATABASE_DEFAULT="hermes"

# ── FHS-style root install layout (set by resolve_install_layout) ──────────
ROOT_FHS_LAYOUT=false
DETECTED_BROWSER_EXECUTABLE=""

# ── Options ─────────────────────────────────────────────────────────────────
USE_VENV=true
RUN_SETUP=true
SKIP_BROWSER=false
SKIP_POSTGRES=false        # NEW: skip docker compose up + alembic upgrade
SKIP_NODE=false            # NEW: skip ui-tui/web npm installs
BRANCH="main"

# Detect non-interactive mode (curl | bash)
if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

# ── Argument parsing ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-venv)         USE_VENV=false; shift ;;
        --skip-setup)      RUN_SETUP=false; shift ;;
        --skip-browser|--no-playwright) SKIP_BROWSER=true; shift ;;
        --skip-node)       SKIP_NODE=true; shift ;;
        --skip-postgres|--no-postgres) SKIP_POSTGRES=true; shift ;;
        --branch)          BRANCH="$2"; shift 2 ;;
        --dir)             INSTALL_DIR="$2"; INSTALL_DIR_EXPLICIT=true; shift 2 ;;
        --hermes-home)     HERMES_HOME="$2"; shift 2 ;;
        --cli-name)        CLI_NAME="$2"; shift 2 ;;
        --pg-dsn)          PG_DSN_OVERRIDE="$2"; shift 2 ;;
        -h|--help)
            cat <<HELP_EOF
Hermes Agent — Cognitive Substrate Edition installer

Usage: install.sh [OPTIONS]

Options:
  --no-venv           Don't create virtual environment
  --skip-setup        Skip interactive setup wizard
  --skip-browser      Skip Playwright/Chromium install
  --skip-node         Skip ui-tui/web npm installs (no TUI / no dashboard)
  --skip-postgres     Skip docker compose up + Alembic migrations
                        Use this if you have your own PostgreSQL and will
                        set HERMES_PG_DSN + run 'alembic upgrade head' yourself
  --branch NAME       Git branch to install (default: main)
  --dir PATH          Installation directory
                        default (non-root): ~/.hermes-substrate/hermes-agent
                        default (root, Linux): /usr/local/lib/hermes-agent
  --hermes-home PATH  Data directory
                        default: ~/.hermes-substrate
                        (Override env: HERMES_HOME)
                        IMPORTANT: defaults to ~/.hermes-substrate (NOT ~/.hermes)
                        so this install does not touch an upstream NousResearch
                        Hermes install. Pass ~/.hermes to share with upstream.
  --cli-name NAME     Name for the CLI shim
                        default: hermes-substrate
                        Pass 'hermes' if you do NOT have an upstream install
                        and want the natural name.
                        (Override env: HERMES_CLI_NAME)
  --pg-dsn URL        PostgreSQL DSN to use
                        default: postgresql://hermes:hermes@localhost:5432/hermes
                        (matches the docker-compose service shipped with this repo)
  -h, --help          Show this help

Side-by-side install with upstream NousResearch/hermes-agent:
  The defaults are tuned to coexist. Your existing ~/.hermes/ data is never
  touched. Your existing 'hermes' command keeps working. Use the new
  'hermes-substrate' command (or 'source venv/bin/activate' then 'hermes')
  to invoke this fork.

Single install (no upstream present):
  curl ... | bash -s -- --cli-name hermes --hermes-home ~/.hermes

Custom PostgreSQL (e.g. your own cluster, Neon, Supabase):
  curl ... | bash -s -- --skip-postgres --pg-dsn 'postgresql://user:pw@host:5432/db'

HELP_EOF
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Helper functions ───────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo -e "${MAGENTA}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│   ⚕ Hermes Agent — Cognitive Substrate Edition          │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  PostgreSQL-backed substrate: skills, memory, recall.   │"
    echo "│  Fork of NousResearch/hermes-agent.                     │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
}

log_info()    { echo -e "${CYAN}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }

prompt_yes_no() {
    local question="$1"
    local default="${2:-yes}"
    local prompt_suffix
    local answer=""

    # bash 3.2-compatible case (macOS /bin/bash)
    case "$default" in
        [yY]|[yY][eE][sS]|[tT][rR][uU][eE]|1) prompt_suffix="[Y/n]" ;;
        *) prompt_suffix="[y/N]" ;;
    esac

    if [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "$question $prompt_suffix " answer || answer=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "%s %s " "$question" "$prompt_suffix" > /dev/tty
        IFS= read -r answer < /dev/tty || answer=""
    else
        answer=""
    fi

    answer="${answer#"${answer%%[![:space:]]*}"}"
    answer="${answer%"${answer##*[![:space:]]}"}"

    if [ -z "$answer" ]; then
        case "$default" in
            [yY]|[yY][eE][sS]|[tT][rR][uU][eE]|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux/files/usr"* ]]
}

# Resolve installation layout. Substrate edition keeps the same layout
# decision tree as upstream but with new defaults.
resolve_install_layout() {
    if [ "$INSTALL_DIR_EXPLICIT" = true ]; then
        log_info "Install directory: $INSTALL_DIR (explicit)"
        return 0
    fi

    if is_termux; then
        INSTALL_DIR="$HERMES_HOME/hermes-agent"
        return 0
    fi

    # Root on Linux: FHS layout, unless a legacy install exists at HERMES_HOME.
    if [ "$OS" = "linux" ] && [ "$(id -u)" -eq 0 ]; then
        if [ -d "$HERMES_HOME/hermes-agent/.git" ]; then
            INSTALL_DIR="$HERMES_HOME/hermes-agent"
            log_info "Existing install detected at $INSTALL_DIR — keeping layout"
            return 0
        fi
        INSTALL_DIR="/usr/local/lib/hermes-agent"
        ROOT_FHS_LAYOUT=true
        log_info "Root install on Linux — using FHS layout"
        log_info "  Code:    $INSTALL_DIR"
        log_info "  Command: /usr/local/bin/$CLI_NAME"
        log_info "  Data:    $HERMES_HOME (unchanged)"
        return 0
    fi

    INSTALL_DIR="$HERMES_HOME/hermes-agent"
}

get_command_link_dir() {
    if is_termux && [ -n "${PREFIX:-}" ]; then
        echo "$PREFIX/bin"
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo "/usr/local/bin"
    else
        echo "$HOME/.local/bin"
    fi
}

get_command_link_display_dir() {
    if is_termux && [ -n "${PREFIX:-}" ]; then
        echo '$PREFIX/bin'
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo '/usr/local/bin'
    else
        echo '~/.local/bin'
    fi
}

get_hermes_command_path() {
    local link_dir
    link_dir="$(get_command_link_dir)"
    if [ -x "$link_dir/$CLI_NAME" ]; then
        echo "$link_dir/$CLI_NAME"
    else
        echo "$CLI_NAME"
    fi
}

# Warn loudly if the side-by-side defaults are being collapsed onto upstream's.
warn_upstream_collision() {
    local upstream_home="$HOME/.hermes"
    local saw_collision=false

    if [ "$HERMES_HOME" = "$upstream_home" ] && [ -d "$upstream_home" ] && [ ! -f "$upstream_home/.substrate_install" ]; then
        log_warn "HERMES_HOME=$upstream_home matches the default for the upstream Hermes Agent install."
        log_warn "  Substrate-backed data (sessions, slices) will go into PostgreSQL, not SQLite,"
        log_warn "  so this is non-destructive — but skills/config/SOUL.md will be SHARED."
        saw_collision=true
    fi

    if [ "$CLI_NAME" = "hermes" ] && command -v hermes >/dev/null 2>&1; then
        local existing
        existing="$(command -v hermes)"
        log_warn "CLI_NAME=hermes will install a launcher at $(get_command_link_display_dir)/hermes"
        log_warn "  which shadows the existing 'hermes' command at: $existing"
        saw_collision=true
    fi

    if [ "$saw_collision" = true ]; then
        if [ "$IS_INTERACTIVE" = true ] || [ -r /dev/tty ]; then
            if ! prompt_yes_no "Continue anyway?" "no"; then
                echo "Aborted. Re-run with default --hermes-home / --cli-name for side-by-side install."
                exit 1
            fi
        else
            log_warn "Non-interactive — proceeding (set --hermes-home and --cli-name explicitly if this is wrong)."
        fi
    fi
}

# ── System detection ───────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)
            if is_termux; then
                OS="android"; DISTRO="termux"
            else
                OS="linux"
                if [ -f /etc/os-release ]; then
                    . /etc/os-release
                    DISTRO="$ID"
                else
                    DISTRO="unknown"
                fi
            fi
            ;;
        Darwin*) OS="macos"; DISTRO="macos" ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"; DISTRO="windows"
            log_error "Windows detected. Please use the PowerShell installer:"
            log_info "  iex (irm https://raw.githubusercontent.com/ggrace519/hermes-agent/main/scripts/install.ps1)"
            exit 1
            ;;
        *) OS="unknown"; DISTRO="unknown"; log_warn "Unknown operating system" ;;
    esac
    log_success "Detected: $OS ($DISTRO)"
}

# ── Dependency checks ──────────────────────────────────────────────────────
install_uv() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Termux detected — using stdlib venv + pip instead of uv"
        UV_CMD=""
        return 0
    fi
    log_info "Checking for uv package manager..."
    if command -v uv &> /dev/null; then
        UV_CMD="uv"; log_success "uv found ($($UV_CMD --version 2>/dev/null))"; return 0
    fi
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_CMD="$HOME/.local/bin/uv"; log_success "uv found at ~/.local/bin ($($UV_CMD --version 2>/dev/null))"; return 0
    fi
    if [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_CMD="$HOME/.cargo/bin/uv"; log_success "uv found at ~/.cargo/bin ($($UV_CMD --version 2>/dev/null))"; return 0
    fi
    log_info "Installing uv (fast Python package manager)..."
    local _log _inst
    _log="$(mktemp 2>/dev/null || echo "/tmp/hermes-uv-install.$$.log")"
    _inst="$(mktemp 2>/dev/null || echo "/tmp/hermes-uv-installer.$$.sh")"
    if ! curl -LsSf https://astral.sh/uv/install.sh -o "$_inst" 2>"$_log"; then
        log_error "Failed to download uv installer"; sed 's/^/    /' "$_log" >&2
        rm -f "$_log" "$_inst"; exit 1
    fi
    if sh "$_inst" >>"$_log" 2>&1; then
        rm -f "$_inst"
        if [ -x "$HOME/.local/bin/uv" ]; then UV_CMD="$HOME/.local/bin/uv"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then UV_CMD="$HOME/.cargo/bin/uv"
        elif command -v uv &> /dev/null; then UV_CMD="uv"
        else log_error "uv installer reported success but binary missing"; sed 's/^/    /' "$_log" >&2; rm -f "$_log"; exit 1
        fi
        rm -f "$_log"
        log_success "uv installed ($($UV_CMD --version 2>/dev/null))"
    else
        log_error "Failed to install uv"; sed 's/^/    /' "$_log" >&2
        rm -f "$_log" "$_inst"; exit 1
    fi
}

check_python() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Checking Termux Python..."
        if command -v python >/dev/null 2>&1; then
            PYTHON_PATH="$(command -v python)"
            if "$PYTHON_PATH" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                log_success "Python found: $("$PYTHON_PATH" --version 2>/dev/null)"; return 0
            fi
        fi
        log_info "Installing Python via pkg..."
        pkg install -y python >/dev/null
        PYTHON_PATH="$(command -v python)"
        log_success "Python installed: $("$PYTHON_PATH" --version 2>/dev/null)"
        return 0
    fi

    log_info "Checking Python $PYTHON_VERSION..."
    if PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null)"; then
        log_success "Python found: $("$PYTHON_PATH" --version 2>/dev/null)"; return 0
    fi
    log_info "Python $PYTHON_VERSION not found, installing via uv..."
    if "$UV_CMD" python install "$PYTHON_VERSION"; then
        PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION")"
        log_success "Python installed: $("$PYTHON_PATH" --version 2>/dev/null)"
    else
        log_error "Failed to install Python $PYTHON_VERSION"; exit 1
    fi
}

check_git() {
    log_info "Checking Git..."
    if command -v git &> /dev/null; then
        log_success "Git $(git --version | awk '{print $3}') found"
        return 0
    fi
    log_error "Git not found"
    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Git via pkg..."
        pkg install -y git >/dev/null
        command -v git >/dev/null 2>&1 && { log_success "Git installed"; return 0; }
    fi
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install git" ;;
                fedora)        log_info "  sudo dnf install git" ;;
                arch)          log_info "  sudo pacman -S git" ;;
                *)             log_info "  Use your package manager to install git" ;;
            esac
            ;;
        android) log_info "  pkg install git" ;;
        macos)   log_info "  xcode-select --install  (or: brew install git)" ;;
    esac
    exit 1
}

# Docker is required for the substrate's PostgreSQL. (Or pass --skip-postgres
# and set HERMES_PG_DSN to your own cluster.)
check_docker() {
    if [ "$SKIP_POSTGRES" = true ]; then
        log_info "Skipping Docker check (--skip-postgres)"
        return 0
    fi
    log_info "Checking Docker (for PostgreSQL substrate)..."
    if ! command -v docker >/dev/null 2>&1; then
        log_error "Docker not found"
        log_info "Substrate needs PostgreSQL. Install Docker Desktop or Docker Engine, then re-run."
        case "$OS" in
            linux)   log_info "  https://docs.docker.com/engine/install/" ;;
            macos)   log_info "  https://docs.docker.com/desktop/install/mac-install/" ;;
            android) log_warn "Docker is not available on Termux. Pass --skip-postgres and use a remote PG." ;;
        esac
        log_info ""
        log_info "Or skip Docker and provide your own PostgreSQL:"
        log_info "  --skip-postgres --pg-dsn 'postgresql://user:pw@host:5432/db'"
        log_info "  (you'll still need to run 'alembic upgrade head' yourself)"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker is installed but not running"
        case "$OS" in
            macos) log_info "  Launch Docker Desktop" ;;
            linux) log_info "  sudo systemctl start docker  (or: sudo service docker start)" ;;
        esac
        exit 1
    fi
    DOCKER_VERSION="$(docker --version | awk '{print $3}' | tr -d ',')"
    log_success "Docker $DOCKER_VERSION found"
    # Compose v2 ships as `docker compose` (subcommand); v1 was `docker-compose`.
    if docker compose version >/dev/null 2>&1; then
        DOCKER_COMPOSE="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE="docker-compose"
    else
        log_error "Docker Compose not found (need v2 'docker compose' or v1 'docker-compose')"
        log_info "Install: https://docs.docker.com/compose/install/"
        exit 1
    fi
    log_success "Docker Compose: $DOCKER_COMPOSE"
}

check_node() {
    if [ "$SKIP_NODE" = true ]; then
        log_info "Skipping Node.js check (--skip-node)"
        HAS_NODE=false
        return 0
    fi
    log_info "Checking Node.js (for TUI + dashboard + browser tools)..."
    if command -v node &> /dev/null; then
        log_success "Node.js $(node --version) found"
        HAS_NODE=true
        return 0
    fi
    if [ -x "$HERMES_HOME/node/bin/node" ]; then
        export PATH="$HERMES_HOME/node/bin:$PATH"
        log_success "Node.js $("$HERMES_HOME/node/bin/node" --version) found (Hermes-managed)"
        HAS_NODE=true
        return 0
    fi
    log_info "Node.js not found — installing Node.js $NODE_VERSION LTS..."
    install_node
}

install_node() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Node.js via pkg..."
        if pkg install -y nodejs >/dev/null; then
            log_success "Node.js $(node --version) installed via pkg"; HAS_NODE=true
        else
            HAS_NODE=false
        fi
        return 0
    fi
    local arch=$(uname -m) node_arch node_os
    case "$arch" in
        x86_64)        node_arch="x64"    ;;
        aarch64|arm64) node_arch="arm64"  ;;
        armv7l)        node_arch="armv7l" ;;
        *) log_warn "Unsupported architecture ($arch)"; HAS_NODE=false; return 0 ;;
    esac
    case "$OS" in
        linux) node_os="linux"  ;;
        macos) node_os="darwin" ;;
        *) log_warn "Unsupported OS"; HAS_NODE=false; return 0 ;;
    esac
    local index_url="https://nodejs.org/dist/latest-v${NODE_VERSION}.x/"
    local tarball_name
    tarball_name=$(curl -fsSL "$index_url" | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.xz" | head -1)
    [ -z "$tarball_name" ] && tarball_name=$(curl -fsSL "$index_url" | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.gz" | head -1)
    if [ -z "$tarball_name" ]; then
        log_warn "Could not find Node.js $NODE_VERSION binary for $node_os-$node_arch"; HAS_NODE=false; return 0
    fi
    local tmp_dir; tmp_dir=$(mktemp -d)
    log_info "Downloading $tarball_name..."
    if ! curl -fsSL "${index_url}${tarball_name}" -o "$tmp_dir/$tarball_name"; then
        log_warn "Download failed"; rm -rf "$tmp_dir"; HAS_NODE=false; return 0
    fi
    log_info "Extracting to $HERMES_HOME/node/..."
    if [[ "$tarball_name" == *.tar.xz ]]; then
        tar xf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    else
        tar xzf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    fi
    local extracted_dir=$(ls -d "$tmp_dir"/node-v* 2>/dev/null | head -1)
    [ ! -d "$extracted_dir" ] && { log_warn "Extraction failed"; rm -rf "$tmp_dir"; HAS_NODE=false; return 0; }
    rm -rf "$HERMES_HOME/node"
    mkdir -p "$HERMES_HOME"
    mv "$extracted_dir" "$HERMES_HOME/node"
    rm -rf "$tmp_dir"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$HERMES_HOME/node/bin/node" "$HOME/.local/bin/node"
    ln -sf "$HERMES_HOME/node/bin/npm"  "$HOME/.local/bin/npm"
    ln -sf "$HERMES_HOME/node/bin/npx"  "$HOME/.local/bin/npx"
    export PATH="$HERMES_HOME/node/bin:$PATH"
    log_success "Node.js $("$HERMES_HOME/node/bin/node" --version) installed to $HERMES_HOME/node/"
    HAS_NODE=true
}

check_network_prerequisites() {
    log_info "Checking internet connectivity..."
    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl not found; skipping connectivity probes"; return 0
    fi
    local failed=false url
    for url in "https://pypi.org/simple/" "https://github.com/"; do
        curl -fsSI --max-time 8 "$url" >/dev/null 2>&1 || { failed=true; log_warn "Could not reach $url"; }
    done
    if [ "$failed" = false ]; then
        log_success "Internet connectivity looks good"
    else
        log_warn "Network checks failed — install may not complete cleanly"
    fi
}

install_system_packages() {
    HAS_RIPGREP=false
    HAS_FFMPEG=false
    log_info "Checking ripgrep (fast file search)..."
    if command -v rg &> /dev/null; then
        log_success "$(rg --version | head -1) found"; HAS_RIPGREP=true
    fi
    log_info "Checking ffmpeg (TTS voice messages)..."
    if command -v ffmpeg &> /dev/null; then
        log_success "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}') found"
        HAS_FFMPEG=true
    fi

    [ "$HAS_RIPGREP" = true ] && [ "$HAS_FFMPEG" = true ] && return 0

    # Termux always needs the build toolchain too.
    if [ "$DISTRO" = "termux" ]; then
        local pkgs=(clang rust make pkg-config libffi openssl ca-certificates curl)
        [ "$HAS_RIPGREP" = false ] && pkgs+=(ripgrep)
        [ "$HAS_FFMPEG" = false ] && pkgs+=(ffmpeg)
        log_info "Installing Termux packages: ${pkgs[*]}"
        pkg install -y "${pkgs[@]}" >/dev/null && {
            command -v rg &>/dev/null && HAS_RIPGREP=true
            command -v ffmpeg &>/dev/null && HAS_FFMPEG=true
        } || log_warn "Could not auto-install all Termux packages"
        return 0
    fi

    local missing=()
    [ "$HAS_RIPGREP" = false ] && missing+=("ripgrep")
    [ "$HAS_FFMPEG" = false ] && missing+=("ffmpeg")

    if [ "$OS" = "macos" ] && command -v brew &> /dev/null; then
        log_info "Installing ${missing[*]} via Homebrew..."
        brew install "${missing[@]}" && {
            command -v rg &>/dev/null && HAS_RIPGREP=true
            command -v ffmpeg &>/dev/null && HAS_FFMPEG=true
        }
        return 0
    fi

    local pkg_install=""
    case "$DISTRO" in
        ubuntu|debian) pkg_install="apt install -y" ;;
        fedora)        pkg_install="dnf install -y" ;;
        arch)          pkg_install="pacman -S --noconfirm" ;;
    esac
    if [ -n "$pkg_install" ]; then
        case "$DISTRO" in
            ubuntu|debian) export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a ;;
        esac
        local install_cmd="$pkg_install ${missing[*]}"
        if [ "$(id -u)" -eq 0 ]; then
            $install_cmd && {
                command -v rg &>/dev/null && HAS_RIPGREP=true
                command -v ffmpeg &>/dev/null && HAS_FFMPEG=true
            }
        elif command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd && {
                command -v rg &>/dev/null && HAS_RIPGREP=true
                command -v ffmpeg &>/dev/null && HAS_FFMPEG=true
            }
        elif command -v sudo &> /dev/null && [ "$IS_INTERACTIVE" = true ]; then
            log_info "sudo needed to install optional system packages: ${missing[*]}"
            if prompt_yes_no "Install via sudo?" "yes"; then
                sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd && {
                    command -v rg &>/dev/null && HAS_RIPGREP=true
                    command -v ffmpeg &>/dev/null && HAS_FFMPEG=true
                }
            fi
        fi
    fi

    [ "$HAS_RIPGREP" = false ] && log_warn "ripgrep missing (file search falls back to grep)"
    [ "$HAS_FFMPEG"  = false ] && log_warn "ffmpeg missing (TTS voice messages limited)"
}

# ── Installation ───────────────────────────────────────────────────────────
clone_repo() {
    log_info "Installing to $INSTALL_DIR..."
    if [ -d "$INSTALL_DIR" ]; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            log_info "Existing installation found, updating..."
            cd "$INSTALL_DIR"
            local autostash_ref=""
            if [ -n "$(git status --porcelain)" ]; then
                local stash_name="hermes-install-autostash-$(date -u +%Y%m%d-%H%M%S)"
                log_info "Local changes detected, stashing before update..."
                git stash push --include-untracked -m "$stash_name"
                autostash_ref="stash@{0}"
            fi
            git fetch origin
            git checkout "$BRANCH"
            git pull --ff-only origin "$BRANCH"
            if [ -n "$autostash_ref" ]; then
                local restore_now="yes"
                if [ -t 0 ] && [ -t 1 ]; then
                    printf "Restore local changes now? [Y/n] "
                    read -r ans
                    case "$ans" in ""|y|Y|yes|YES|Yes) restore_now="yes" ;; *) restore_now="no" ;; esac
                fi
                if [ "$restore_now" = "yes" ]; then
                    git stash apply "$autostash_ref" && git stash drop "$autostash_ref" >/dev/null \
                        && log_warn "Local changes restored — review git status if behavior is unexpected"
                else
                    log_info "Local changes preserved in git stash ($autostash_ref)"
                fi
            fi
        else
            log_error "Directory exists but is not a git repository: $INSTALL_DIR"
            log_info "Remove it or choose a different directory with --dir"
            exit 1
        fi
    else
        log_info "Trying SSH clone..."
        if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" \
           git clone --branch "$BRANCH" "$REPO_URL_SSH" "$INSTALL_DIR" 2>/dev/null; then
            log_success "Cloned via SSH"
        else
            rm -rf "$INSTALL_DIR" 2>/dev/null
            log_info "SSH failed, trying HTTPS..."
            git clone --branch "$BRANCH" "$REPO_URL_HTTPS" "$INSTALL_DIR" || { log_error "git clone failed"; exit 1; }
            log_success "Cloned via HTTPS"
        fi
    fi
    cd "$INSTALL_DIR"
    log_success "Repository ready"
}

setup_venv() {
    if [ "$USE_VENV" = false ]; then
        log_info "Skipping virtual environment (--no-venv)"; return 0
    fi
    if [ -d "venv" ]; then
        log_info "Virtual environment already exists, recreating..."
        rm -rf venv
    fi
    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating virtual environment with Termux Python..."
        "$PYTHON_PATH" -m venv venv
    else
        log_info "Creating virtual environment with Python $PYTHON_VERSION..."
        $UV_CMD venv venv --python "$PYTHON_VERSION"
    fi
    log_success "Virtual environment ready ($(./venv/bin/python --version 2>/dev/null))"
}

install_deps() {
    log_info "Installing Python dependencies (this can take 1-5 minutes on first run)..."
    if [ "$USE_VENV" = true ]; then
        export VIRTUAL_ENV="$INSTALL_DIR/venv"
    fi

    # Termux path keeps the upstream pip+constraints flow (uv's not viable there).
    if [ "$DISTRO" = "termux" ]; then
        local PIP_PYTHON
        [ "$USE_VENV" = true ] && PIP_PYTHON="$INSTALL_DIR/venv/bin/python" || PIP_PYTHON="$PYTHON_PATH"
        if [ -z "${ANDROID_API_LEVEL:-}" ]; then
            ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
            export ANDROID_API_LEVEL
        fi
        "$PIP_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null
        if "$PIP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.platform == "android" else 1)' 2>/dev/null; then
            log_info "Android Python detected: prebuilding psutil compatibility shim..."
            "$PIP_PYTHON" "$INSTALL_DIR/scripts/install_psutil_android.py" --pip "$PIP_PYTHON -m pip" \
                || log_warn "psutil Android prebuild failed"
        fi
        if ! "$PIP_PYTHON" -m pip install -e '.[termux-all]' -c constraints-termux.txt; then
            "$PIP_PYTHON" -m pip install -e '.[termux]' -c constraints-termux.txt \
                || "$PIP_PYTHON" -m pip install -e '.' -c constraints-termux.txt \
                || { log_error "Termux pip install failed"; exit 1; }
        fi
        log_success "Python deps installed (Termux profile)"
        return 0
    fi

    # Hash-verified install via uv.lock (preferred). Fork's lockfile is the
    # source of truth for substrate/asyncpg/pgvector resolution — never
    # resolve those transitives on the fly.
    if [ -f "uv.lock" ]; then
        log_info "Installing curated [all] extra (hash-verified via uv.lock)..."
        if UV_PROJECT_ENVIRONMENT="$INSTALL_DIR/venv" $UV_CMD sync --extra all --locked; then
            log_success "Python deps installed (hash-verified)"
            return 0
        fi
        log_warn "uv.lock sync failed (see uv output above), falling back to PyPI resolve"
    fi

    # Fallback: PyPI resolve. Won't hash-verify but keeps installs working
    # if uv.lock is stale relative to pyproject.toml.
    if $UV_CMD pip install -e '.[all]'; then
        log_success "Python deps installed (PyPI resolve)"
    elif $UV_CMD pip install -e '.'; then
        log_warn "Installed core only — optional extras failed; some features off"
    else
        log_error "Python install failed even at core level"
        log_info "Check build tools: sudo apt install build-essential python3-dev libffi-dev"
        exit 1
    fi
}

# ── PostgreSQL via docker compose ──────────────────────────────────────────

# Detect a non-substrate PostgreSQL listening on the chosen port. If a native
# pg server (apt-installed `postgresql` is common on Ubuntu) is bound to
# 5432 *and* it doesn't accept our `hermes/hermes` creds, our docker
# container will silently fail to bind (or bind on a different interface)
# and every connection from the host will hit the native one and bounce
# with InvalidPasswordError. Probe first; if the port is taken by something
# other than our container, bump to the next free port and pin everything
# downstream to that port.
choose_pg_port() {
    if [ "$SKIP_POSTGRES" = true ]; then
        return 0
    fi

    # If --pg-dsn was passed, extract its host:port and use that for compose
    # binding too. The DSN tells us where to *connect*; if it points at
    # localhost:5434, our docker container must also bind to host port 5434
    # or alembic will hit a closed socket.
    if [ -n "${PG_DSN_OVERRIDE:-}" ]; then
        # Parse port from postgresql://user:pw@host:PORT/db. Tolerates missing
        # port (postgresql defaults to 5432) and missing user/pw segments.
        local dsn_port
        dsn_port=$(echo "$PG_DSN_OVERRIDE" | sed -nE 's|.*@[^:/]+:([0-9]+)/.*|\1|p')
        if [ -n "$dsn_port" ]; then
            PG_PORT_DEFAULT="$dsn_port"
            export POSTGRES_PORT="$dsn_port"
            log_info "PostgreSQL: --pg-dsn pins host port $dsn_port"
        fi
        return 0
    fi

    local port="$PG_PORT_DEFAULT"

    # Helper: does *something* answer a TCP connect on this port?
    _port_in_use() {
        local p="$1"
        # Prefer ss (always installed on Linux), then lsof, then bash /dev/tcp.
        if command -v ss >/dev/null 2>&1; then
            ss -tlnH "( sport = :$p )" 2>/dev/null | grep -q ':'
        elif command -v lsof >/dev/null 2>&1; then
            lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1
        else
            (timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$p" 2>/dev/null) && return 0 || return 1
        fi
    }

    # Helper: is the listener on this port actually OUR docker container?
    # If yes, no port change needed — `docker compose up -d` will keep it.
    _port_is_our_container() {
        local p="$1"
        docker port hermes-agent-postgres-1 2>/dev/null \
            | grep -qE "5432/tcp -> 0\\.0\\.0\\.0:$p|5432/tcp -> \\[::]:$p"
    }

    if _port_in_use "$port"; then
        if _port_is_our_container "$port"; then
            log_info "PostgreSQL: port $port already used by our container — reusing"
        else
            log_warn "PostgreSQL: port $port is taken by something else (likely a native"
            log_warn "  Postgres install — apt-installed postgresql, system service, etc.)"
            # Pick the next free port between 5433 and 5450.
            local p
            for p in $(seq 5433 5450); do
                if ! _port_in_use "$p"; then
                    port="$p"
                    log_info "PostgreSQL: bumping to port $port to avoid collision"
                    break
                fi
            done
            if [ "$port" = "$PG_PORT_DEFAULT" ]; then
                log_error "No free port in 5433-5450; aborting."
                log_info "Free a port or pass --pg-dsn pointing at an existing cluster."
                exit 1
            fi
        fi
    fi

    PG_PORT_DEFAULT="$port"
    # Re-publish the env-var so docker compose's port mapping picks it up.
    export POSTGRES_PORT="$port"
}

setup_postgres() {
    if [ "$SKIP_POSTGRES" = true ]; then
        log_info "Skipping PostgreSQL setup (--skip-postgres)"
        if [ -z "${PG_DSN_OVERRIDE:-}" ]; then
            log_warn "You'll need to set HERMES_PG_DSN yourself and run 'alembic upgrade head'"
        fi
        return 0
    fi

    choose_pg_port

    log_info "Starting PostgreSQL via docker compose (host port $PG_PORT_DEFAULT → container 5432)..."
    cd "$INSTALL_DIR"

    # docker-compose.yml ships with `postgres` (port 5432) + `postgres-test`
    # (port 5433, under `test` profile, only used by pytest). We only want
    # the real `postgres` service running for production use.
    if ! $DOCKER_COMPOSE up -d postgres; then
        log_error "Failed to start postgres service"
        log_info "Inspect: $DOCKER_COMPOSE logs postgres"
        exit 1
    fi

    log_info "Waiting for PostgreSQL to be healthy..."
    local i
    for i in $(seq 1 60); do
        if $DOCKER_COMPOSE ps postgres 2>/dev/null | grep -q "healthy"; then
            log_success "PostgreSQL is ready"
            break
        fi
        if [ "$i" -eq 60 ]; then
            log_error "PostgreSQL did not become healthy within 60s"
            log_info "Inspect: $DOCKER_COMPOSE logs postgres"
            exit 1
        fi
        sleep 1
    done
}

run_migrations() {
    if [ "$SKIP_POSTGRES" = true ] && [ -z "${PG_DSN_OVERRIDE:-}" ]; then
        log_info "Skipping Alembic migrations (no DSN configured)"
        return 0
    fi
    local dsn="${PG_DSN_OVERRIDE:-postgresql://${PG_USER_DEFAULT}:${PG_PASSWORD_DEFAULT}@${PG_HOST_DEFAULT}:${PG_PORT_DEFAULT}/${PG_DATABASE_DEFAULT}}"
    log_info "Running Alembic migrations against:"
    log_info "  $dsn"
    cd "$INSTALL_DIR"
    if HERMES_PG_DSN="$dsn" ./venv/bin/alembic -c migrations/alembic.ini upgrade head; then
        log_success "Substrate schema migrated to head"
    else
        log_error "Alembic upgrade failed"
        log_info "Check connectivity: HERMES_PG_DSN=\"$dsn\" ./venv/bin/python -c 'import asyncpg, asyncio; asyncio.run(asyncpg.connect(\"$dsn\"))'"
        exit 1
    fi
}

# ── PATH wiring (CLI launcher) ─────────────────────────────────────────────
setup_path() {
    log_info "Setting up $CLI_NAME command..."

    if [ "$USE_VENV" = true ]; then
        HERMES_BIN="$INSTALL_DIR/venv/bin/hermes"
    else
        HERMES_BIN="$(which hermes 2>/dev/null || echo "")"
        [ -z "$HERMES_BIN" ] && { log_warn "hermes not on PATH"; return 0; }
    fi

    if [ ! -x "$HERMES_BIN" ]; then
        log_warn "hermes entry point not found at $HERMES_BIN"
        log_info "Re-run: cd $INSTALL_DIR && $UV_CMD pip install -e '.[all]'"
        return 0
    fi

    local link_dir=$(get_command_link_dir)
    local link_disp=$(get_command_link_display_dir)
    mkdir -p "$link_dir"
    rm -f "$link_dir/$CLI_NAME"

    # Launcher injects HERMES_PG_DSN if --skip-postgres wasn't used.
    # Clears PYTHONPATH/PYTHONHOME so a parent process can't shadow this venv.
    local pg_dsn="${PG_DSN_OVERRIDE:-postgresql://${PG_USER_DEFAULT}:${PG_PASSWORD_DEFAULT}@${PG_HOST_DEFAULT}:${PG_PORT_DEFAULT}/${PG_DATABASE_DEFAULT}}"
    cat > "$link_dir/$CLI_NAME" <<EOF
#!/usr/bin/env bash
# Launcher generated by Hermes Substrate installer.
# Do not edit by hand — re-run install.sh to regenerate.
unset PYTHONPATH
unset PYTHONHOME
export HERMES_HOME="\${HERMES_HOME:-$HERMES_HOME}"
export HERMES_PG_DSN="\${HERMES_PG_DSN:-$pg_dsn}"
exec "$HERMES_BIN" "\$@"
EOF
    chmod +x "$link_dir/$CLI_NAME"
    log_success "Launcher installed → $link_disp/$CLI_NAME"

    if [ "$DISTRO" = "termux" ]; then
        export PATH="$link_dir:$PATH"
        return 0
    fi

    if [ "$ROOT_FHS_LAYOUT" = true ]; then
        export PATH="$link_dir:$PATH"
        if env -i HOME="$HOME" TERM="${TERM:-dumb}" bash -i -c "command -v $CLI_NAME" >/dev/null 2>&1; then
            log_info "/usr/local/bin is on PATH for all shells"
            return 0
        fi
        log_info "/usr/local/bin missing from non-login shells (RHEL-family); fixing ~/.bashrc"
        local PATH_LINE='export PATH="/usr/local/bin:$PATH"'
        for cfg in "$HOME/.bashrc" "$HOME/.bash_profile"; do
            [ -f "$cfg" ] || continue
            grep -v '^[[:space:]]*#' "$cfg" 2>/dev/null | grep -qE 'PATH=.*(/usr/local/bin|\$link_dir)' || {
                printf '\n# Hermes Substrate — ensure /usr/local/bin is on PATH\n%s\n' "$PATH_LINE" >> "$cfg"
                log_success "Added /usr/local/bin to $cfg"
            }
        done
        return 0
    fi

    # User-scoped: ensure ~/.local/bin on PATH for the user's actual login shell.
    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$link_dir$"; then
        local LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        local PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
        local cfgs=()
        case "$LOGIN_SHELL" in
            zsh)  [ -f "$HOME/.zshrc"     ] && cfgs+=("$HOME/.zshrc")
                  [ -f "$HOME/.zprofile"  ] && cfgs+=("$HOME/.zprofile")
                  [ ${#cfgs[@]} -eq 0 ] && { touch "$HOME/.zshrc"; cfgs+=("$HOME/.zshrc"); } ;;
            bash) [ -f "$HOME/.bashrc"       ] && cfgs+=("$HOME/.bashrc")
                  [ -f "$HOME/.bash_profile" ] && cfgs+=("$HOME/.bash_profile") ;;
            fish)
                local FISH_CONFIG="$HOME/.config/fish/config.fish"
                mkdir -p "$(dirname "$FISH_CONFIG")"
                touch "$FISH_CONFIG"
                grep -q 'fish_add_path.*\.local/bin' "$FISH_CONFIG" || {
                    printf '\n# Hermes Substrate — ensure ~/.local/bin is on PATH\nfish_add_path "$HOME/.local/bin"\n' >> "$FISH_CONFIG"
                    log_success "Added ~/.local/bin to $FISH_CONFIG"
                }
                ;;
            *)    [ -f "$HOME/.bashrc" ] && cfgs+=("$HOME/.bashrc")
                  [ -f "$HOME/.zshrc"  ] && cfgs+=("$HOME/.zshrc") ;;
        esac
        [ -f "$HOME/.profile" ] && cfgs+=("$HOME/.profile")
        local cfg
        for cfg in "${cfgs[@]}"; do
            grep -v '^[[:space:]]*#' "$cfg" 2>/dev/null | grep -qE 'PATH=.*\.local/bin' || {
                printf '\n# Hermes Substrate — ensure ~/.local/bin is on PATH\n%s\n' "$PATH_LINE" >> "$cfg"
                log_success "Added ~/.local/bin to $cfg"
            }
        done
    fi
    export PATH="$link_dir:$PATH"
}

copy_config_templates() {
    log_info "Setting up configuration files in $HERMES_HOME..."
    mkdir -p "$HERMES_HOME"/{cron,sessions,logs,pairing,hooks,image_cache,audio_cache,memories,skills}

    if [ ! -f "$HERMES_HOME/.env" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
        else
            touch "$HERMES_HOME/.env"
        fi
        log_success "Created $HERMES_HOME/.env"
    else
        log_info "$HERMES_HOME/.env exists, keeping it"
    fi
    chmod 600 "$HERMES_HOME/.env"

    # Ensure HERMES_PG_DSN is in .env so non-launcher entry points (gateway,
    # cron jobs spawned outside the shim) can find the database.
    local pg_dsn="${PG_DSN_OVERRIDE:-postgresql://${PG_USER_DEFAULT}:${PG_PASSWORD_DEFAULT}@${PG_HOST_DEFAULT}:${PG_PORT_DEFAULT}/${PG_DATABASE_DEFAULT}}"
    if ! grep -q '^HERMES_PG_DSN=' "$HERMES_HOME/.env" 2>/dev/null; then
        printf '\n# Substrate PostgreSQL DSN (added by installer)\nHERMES_PG_DSN=%s\n' "$pg_dsn" >> "$HERMES_HOME/.env"
        log_success "Wrote HERMES_PG_DSN to $HERMES_HOME/.env"
    fi

    if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
        cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
        log_success "Created $HERMES_HOME/config.yaml"
    fi

    if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
        cat > "$HERMES_HOME/SOUL.md" <<'SOUL_EOF'
# Hermes Agent Persona

<!--
This file defines the agent's personality and tone.
Edit to customize how Hermes communicates with you.
Loaded fresh each message — no restart needed.
-->
SOUL_EOF
        log_success "Created $HERMES_HOME/SOUL.md"
    fi

    # Marker so warn_upstream_collision can tell if HERMES_HOME has previously
    # been used by a substrate install (avoid the warning on re-installs).
    touch "$HERMES_HOME/.substrate_install"

    log_info "Syncing bundled skills..."
    if [ -x "$INSTALL_DIR/venv/bin/python" ] && [ -f "$INSTALL_DIR/tools/skills_sync.py" ]; then
        "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py" 2>/dev/null \
            && log_success "Skills synced" \
            || log_info "Skills sync skipped (will run on first $CLI_NAME invocation)"
    fi

    configure_browser_env_from_system_browser
}

# ── Browser tools (Playwright) ─────────────────────────────────────────────
find_system_browser() {
    if [ -n "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ]; then
        if [ -x "$AGENT_BROWSER_EXECUTABLE_PATH" ]; then echo "$AGENT_BROWSER_EXECUTABLE_PATH"; return 0; fi
        command -v "$AGENT_BROWSER_EXECUTABLE_PATH" 2>/dev/null && return 0
    fi
    local c
    for c in google-chrome google-chrome-stable chromium chromium-browser chrome; do
        command -v "$c" 2>/dev/null && return 0
    done
    if [ "$(uname)" = "Darwin" ]; then
        for app in \
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
            "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
            [ -x "$app" ] && { echo "$app"; return 0; }
        done
    fi
    return 1
}

configure_browser_env_from_system_browser() {
    local env_file="$HERMES_HOME/.env"
    local browser_path="${DETECTED_BROWSER_EXECUTABLE:-$(find_system_browser 2>/dev/null || true)}"
    [ -z "$browser_path" ] && return 0
    [ -f "$env_file" ] || touch "$env_file"
    grep -q '^AGENT_BROWSER_EXECUTABLE_PATH=' "$env_file" 2>/dev/null && return 0
    printf '\n# Use the system Chrome/Chromium for browser tools.\nAGENT_BROWSER_EXECUTABLE_PATH=%s\n' "$browser_path" >> "$env_file"
    log_success "Browser tools will use $browser_path"
}

run_browser_install_with_timeout() {
    local seconds="$1"; shift
    if command -v timeout >/dev/null 2>&1; then timeout "$seconds" "$@"; else "$@"; fi
}

install_node_deps() {
    if [ "$HAS_NODE" = false ]; then
        log_info "Skipping Node.js dependencies (Node not installed)"
        return 0
    fi
    if [ "$DISTRO" = "termux" ]; then
        log_info "Termux: skipping ui-tui/web npm installs (not part of tested Termux path)"
        return 0
    fi

    cd "$INSTALL_DIR"
    if [ -f "package.json" ]; then
        log_info "Installing root Node.js dependencies (browser tools)..."
        npm install --silent 2>/dev/null || log_warn "Root npm install failed (browser tools may not work)"
    fi

    if [ "$SKIP_BROWSER" = true ]; then
        log_info "Skipping Playwright/Chromium install (--skip-browser)"
    else
        log_info "Installing browser engine (Playwright Chromium)..."
        DETECTED_BROWSER_EXECUTABLE="$(find_system_browser 2>/dev/null || true)"
        if [ -n "$DETECTED_BROWSER_EXECUTABLE" ]; then
            log_success "Found system browser at $DETECTED_BROWSER_EXECUTABLE — skipping Chromium download"
        else
            case "$DISTRO" in
                ubuntu|debian|raspbian|pop|linuxmint|elementary|zorin|kali|parrot)
                    if [ "$(id -u)" -eq 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null); then
                        run_browser_install_with_timeout 600 npx playwright install --with-deps chromium 2>/dev/null \
                            || log_warn "Playwright install failed — browser tools will not work"
                    else
                        log_warn "No sudo — installing Chromium only (admin must run later: sudo npx playwright install-deps chromium)"
                        run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null \
                            || log_warn "Playwright install failed"
                    fi
                    ;;
                arch|manjaro|cachyos|endeavouros|garuda)
                    if [ "$(id -u)" -eq 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null); then
                        local sudo_pfx=""; [ "$(id -u)" -ne 0 ] && sudo_pfx="sudo "
                        ${sudo_pfx}pacman -S --noconfirm --needed nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib >/dev/null 2>&1 || true
                    fi
                    run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null \
                        || log_warn "Playwright install failed"
                    ;;
                fedora|rhel|centos|rocky|alma)
                    log_warn "RPM distro: install system deps manually if missing:"
                    log_info "  sudo dnf install nss atk at-spi2-core cups-libs libdrm libxkbcommon mesa-libgbm pango cairo alsa-lib"
                    run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || true
                    ;;
                *)
                    run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || true
                    ;;
            esac
        fi
    fi

    if [ -f "$INSTALL_DIR/ui-tui/package.json" ]; then
        log_info "Installing TUI dependencies..."
        cd "$INSTALL_DIR/ui-tui"
        npm install --silent 2>/dev/null || log_warn "TUI npm install failed ($CLI_NAME --tui may not work)"
    fi
    log_success "Node dependencies installed"
}

# ── Substrate smoke ────────────────────────────────────────────────────────
# Verify the substrate actually boots against PG. Fails loudly if migrations
# didn't run or PG isn't reachable, which catches misconfigured DSNs before
# the setup wizard or first chat session does.
substrate_smoke() {
    if [ "$SKIP_POSTGRES" = true ] && [ -z "${PG_DSN_OVERRIDE:-}" ]; then
        log_info "Skipping substrate smoke (no DSN configured)"
        return 0
    fi
    log_info "Running substrate boot smoke test..."
    local dsn="${PG_DSN_OVERRIDE:-postgresql://${PG_USER_DEFAULT}:${PG_PASSWORD_DEFAULT}@${PG_HOST_DEFAULT}:${PG_PORT_DEFAULT}/${PG_DATABASE_DEFAULT}}"
    local script_out
    script_out=$(HERMES_PG_DSN="$dsn" "$INSTALL_DIR/venv/bin/python" - <<'PY' 2>&1
import asyncio, os, sys
async def main():
    import hermes_db
    from hermes_bootstrap import bootstrap_substrate
    await hermes_db.init(os.environ["HERMES_PG_DSN"])
    sub = await bootstrap_substrate()
    if sub is None:
        print("substrate-boot-FAIL: bootstrap_substrate returned None")
        sys.exit(2)
    print(f"substrate-boot-OK type={type(sub).__name__}")
    await hermes_db.close()
asyncio.run(main())
PY
)
    if echo "$script_out" | grep -q "substrate-boot-OK"; then
        log_success "Substrate boots cleanly against PostgreSQL"
    else
        log_warn "Substrate boot smoke FAILED — first chat session may emit warnings"
        echo "$script_out" | sed 's/^/    /' >&2
    fi
}

# ── Setup wizard ───────────────────────────────────────────────────────────
run_setup_wizard() {
    if [ "$RUN_SETUP" = false ]; then
        log_info "Skipping setup wizard (--skip-setup) — run '$CLI_NAME setup' later"
        return 0
    fi
    if ! (: </dev/tty) 2>/dev/null; then
        log_info "No TTY — skipping wizard. Run '$CLI_NAME setup' interactively when you have one."
        return 0
    fi
    echo ""
    log_info "Starting setup wizard..."
    cd "$INSTALL_DIR"
    local pg_dsn="${PG_DSN_OVERRIDE:-postgresql://${PG_USER_DEFAULT}:${PG_PASSWORD_DEFAULT}@${PG_HOST_DEFAULT}:${PG_PORT_DEFAULT}/${PG_DATABASE_DEFAULT}}"
    HERMES_HOME="$HERMES_HOME" HERMES_PG_DSN="$pg_dsn" \
        "$INSTALL_DIR/venv/bin/python" -m hermes_cli.main setup < /dev/tty
}

# ── Success message ────────────────────────────────────────────────────────
print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│              ✓ Installation Complete!                   │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}📁 Your files:${NC}"
    echo -e "   ${YELLOW}Code:${NC}      $INSTALL_DIR"
    echo -e "   ${YELLOW}Data:${NC}      $HERMES_HOME"
    echo -e "   ${YELLOW}Config:${NC}    $HERMES_HOME/config.yaml"
    echo -e "   ${YELLOW}API keys:${NC}  $HERMES_HOME/.env"
    echo ""

    if [ "$SKIP_POSTGRES" = false ]; then
        echo -e "${CYAN}${BOLD}🗄  PostgreSQL (substrate):${NC}"
        echo -e "   $DOCKER_COMPOSE ps postgres   # check status"
        echo -e "   $DOCKER_COMPOSE logs postgres # inspect logs"
        echo -e "   $DOCKER_COMPOSE stop postgres # shut down (will not auto-restart)"
        echo ""
    fi

    echo -e "${CYAN}${BOLD}🚀 Commands:${NC}"
    echo -e "   ${GREEN}$CLI_NAME${NC}                Start chatting"
    echo -e "   ${GREEN}$CLI_NAME setup${NC}          Configure API keys & settings"
    echo -e "   ${GREEN}$CLI_NAME config${NC}         View/edit configuration"
    echo -e "   ${GREEN}$CLI_NAME substrate inspect${NC}  Show running sub-agents + stream stats"
    echo -e "   ${GREEN}$CLI_NAME gateway install${NC}    Background gateway service (messaging + cron)"
    echo ""

    if [ "$CLI_NAME" != "hermes" ]; then
        echo -e "${BLUE}ℹ${NC}  Installed as ${BOLD}$CLI_NAME${NC} to avoid colliding with any existing upstream"
        echo "    Hermes install. Pass ${BOLD}--cli-name hermes${NC} on a clean machine to use the natural name."
        echo ""
    fi

    if [ "$DISTRO" = "termux" ]; then
        echo -e "${YELLOW}⚡ '$CLI_NAME' was linked into $(get_command_link_display_dir) (on PATH in Termux).${NC}"
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo -e "${YELLOW}⚡ '$CLI_NAME' linked into /usr/local/bin — ready to use, no reload needed.${NC}"
    else
        echo -e "${YELLOW}⚡ Reload your shell to use '$CLI_NAME':${NC}"
        case "$(basename "${SHELL:-/bin/bash}")" in
            zsh)  echo "   source ~/.zshrc" ;;
            bash) echo "   source ~/.bashrc" ;;
            fish) echo "   source ~/.config/fish/config.fish" ;;
            *)    echo "   source ~/.bashrc  # or ~/.zshrc" ;;
        esac
    fi
    echo ""

    [ "$HAS_NODE" = false ]    && echo -e "${YELLOW}Note: Node.js missing — browser tools / TUI / dashboard disabled.${NC}"
    [ "$HAS_RIPGREP" = false ] && echo -e "${YELLOW}Note: ripgrep missing — file search uses grep fallback.${NC}"
}

# ── Main ───────────────────────────────────────────────────────────────────
main() {
    print_banner

    detect_os
    resolve_install_layout
    warn_upstream_collision

    install_uv
    check_python
    check_git
    check_docker
    check_node
    check_network_prerequisites
    install_system_packages

    clone_repo
    setup_venv
    install_deps
    setup_postgres
    run_migrations
    install_node_deps
    setup_path
    copy_config_templates
    substrate_smoke
    run_setup_wizard

    print_success

    echo "git" > "$HERMES_HOME/.install_method"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cli_name=$CLI_NAME install_dir=$INSTALL_DIR" >> "$HERMES_HOME/.install_log"
}

main
