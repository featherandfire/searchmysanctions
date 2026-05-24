"""
Regression tests for settings.py — specifically the prod fail-fast guard.

Each test runs `python -c "import settings"` in a subprocess so its env vars
and module-load behaviour stay isolated from the main pytest process (which
already has `settings` imported with non-Fly defaults).
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_settings(env: dict) -> subprocess.CompletedProcess:
    """Run `python -c 'import settings'` in a clean env, return the process."""
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import settings"],
        env=full_env,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_fail_fast_on_fly_without_secret_key():
    """FLY_APP_NAME set + SECRET_KEY unset → import must raise."""
    result = _import_settings({"FLY_APP_NAME": "all-sanctions"})
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_fail_fast_on_fly_with_dev_default_secret_key():
    """Even if SECRET_KEY is explicitly set to the dev default, refuse to boot."""
    result = _import_settings({
        "FLY_APP_NAME": "all-sanctions",
        "SECRET_KEY":   "dev-secret-do-not-use-in-prod",
    })
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_boots_on_fly_with_real_secret_key():
    """A real SECRET_KEY in the Fly env is fine."""
    result = _import_settings({
        "FLY_APP_NAME": "all-sanctions",
        "SECRET_KEY":   "some-actual-production-secret",
    })
    assert result.returncode == 0, result.stderr


def test_boots_locally_without_secret_key():
    """No FLY_APP_NAME → no fail-fast, dev default is acceptable."""
    result = _import_settings({})
    assert result.returncode == 0, result.stderr
