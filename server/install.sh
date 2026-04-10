#!/usr/bin/env bash
# install.sh — install/upgrade the semantic MCP server into the project venv
# Usage: bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PIP="$VENV/bin/pip"
PYTHON="$VENV/bin/python"

ROCM_SITE_PYTHON="${ROCM_SITE_PYTHON:-python3}"

detect_rocm_host() {
    "$ROCM_SITE_PYTHON" - <<'PY'
import sys
try:
    import torch
except Exception:
    sys.exit(1)
ok = bool(torch.cuda.is_available() and getattr(torch.version, "hip", None))
sys.exit(0 if ok else 1)
PY
}

if [[ ! -f "$PIP" ]]; then
    if detect_rocm_host; then
        echo "==> Creating venv with --system-site-packages to reuse ROCm PyTorch from $ROCM_SITE_PYTHON"
        "$ROCM_SITE_PYTHON" -m venv --system-site-packages "$VENV"
    else
        echo "==> Creating standard venv"
        python3 -m venv "$VENV"
    fi
fi

echo "==> Using venv: $VENV"
echo "==> Python: $($PYTHON --version)"
echo ""

echo "==> Upgrading pip..."
"$PIP" install -q --upgrade pip

if detect_rocm_host; then
    echo "==> ROCm host detected; preserving system ROCm torch packages"
    echo "==> Host torch: $("$ROCM_SITE_PYTHON" - <<'PY'
import torch, torchvision
print(f"{torch.__version__} / {torchvision.__version__} / hip={getattr(torch.version, 'hip', None)}")
PY
)"
fi

echo "==> Installing project + all dependencies (pyproject.toml)..."
"$PIP" install -q -e "$SCRIPT_DIR"

echo "==> Installing dev extras..."
"$PIP" install -q -e "$SCRIPT_DIR[dev]"

echo ""
echo "==> Verifying key packages..."
"$VENV/bin/python" -c "
packages = ['lancedb', 'pyarrow', 'mcp', 'watchdog', 'transformers', 'torch', 'torchvision', 'tree_sitter_python', 'peft', 'PIL', 'requests']
ok = True
for pkg in packages:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, '__version__', '?')
        print(f'  OK  {pkg} {ver}')
    except ImportError as e:
        print(f'  MISSING  {pkg}: {e}')
        ok = False
import torch, platform
cuda = torch.cuda.is_available()
hip  = getattr(torch.version, 'hip', None)
mps  = getattr(torch.backends, 'mps', None)
mps_ok = bool(mps and mps.is_available() and mps.is_built())
if cuda and hip:
    print(f'  Accelerator: ROCm (HIP {hip})')
elif cuda:
    print(f'  Accelerator: CUDA ({torch.cuda.get_device_name(0)})')
elif mps_ok:
    print(f'  Accelerator: MPS (Apple {platform.machine()})')
else:
    print('  Accelerator: CPU only')
import sys; sys.exit(0 if ok else 1)
"

echo ""
echo "Done. Activate with: source .venv/bin/activate"
