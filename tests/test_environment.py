import sys
import importlib

def test_python_version():
    assert sys.version_info >= (3, 12), f"Expected 3.12+, got {sys.version_info}"

def test_venv_active():
    assert "signal_engine_v1/venv" in sys.executable, (
        f"Not running inside venv. Python is: {sys.executable}\n"
        f"Run: source venv/bin/activate"
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
