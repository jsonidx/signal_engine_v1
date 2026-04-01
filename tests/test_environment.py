import sys
import importlib

def test_python_version():
    assert sys.version_info >= (3, 12), f"Expected 3.12+, got {sys.version_info}"

def test_venv_active():
    # Accept project venv OR pyenv/system Python (e.g., when using pyenv)
    in_project_venv = "signal_engine_v1/venv" in sys.executable
    in_pyenv = ".pyenv" in sys.executable
    in_venv = sys.prefix != sys.base_prefix  # generic venv check
    assert in_project_venv or in_pyenv or in_venv, (
        f"Not running inside a virtual environment. Python is: {sys.executable}\n"
        f"Run: source venv/bin/activate  (or use pyenv)"
    )

def test_core_dependencies_importable():
    required = ["pytest", "yfinance", "pandas", "numpy", "sqlite3", "requests"]
    for module in required:
        assert importlib.util.find_spec(module) is not None, (
            f"Module '{module}' not importable — run: pip install -r requirements.txt"
        )

def test_dev_dependencies_importable():
    dev_required = ["pytest_mock", "responses", "freezegun"]
    for module in dev_required:
        assert importlib.util.find_spec(module) is not None, (
            f"Dev module '{module}' not importable — run: pip install -r requirements-dev.txt"
        )
