"""Project setup: optional .env loading, and no secrets in committable files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.hospital_2.semantic import SemanticConfig
from src.main import load_environment

ROOT = Path(__file__).resolve().parents[1]
VAR = "H2_SETUP_TEST_VARIABLE"


@pytest.fixture()
def scratch_var():
    os.environ.pop(VAR, None)
    yield VAR
    os.environ.pop(VAR, None)


def test_startup_loads_a_dotenv_file(tmp_path, scratch_var):
    env = tmp_path / ".env"
    env.write_text(f"{scratch_var}=from-dotenv\n", encoding="utf-8")
    assert load_environment(env) is True
    assert os.environ[scratch_var] == "from-dotenv"


def test_real_os_environment_variables_win(tmp_path, scratch_var):
    os.environ[scratch_var] = "from-os"
    env = tmp_path / ".env"
    env.write_text(f"{scratch_var}=from-dotenv\n", encoding="utf-8")
    load_environment(env)
    assert os.environ[scratch_var] == "from-os"


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    assert load_environment(tmp_path / "does-not-exist.env") is False


def test_placeholder_dotenv_keeps_the_defaults(tmp_path, monkeypatch):
    """Empty ``KEY=`` placeholders mean "unset", not "empty override"."""
    names = [l.split("=")[0] for l in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
             if l and not l.startswith("#")]
    for name in names + ["JEV_VERSION"]:
        monkeypatch.delenv(name, raising=False)
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    load_environment(tmp_path / ".env")
    try:
        c = SemanticConfig.from_env()
        assert c.openrouter_api_key is None and c.jev_api_key is None
        assert c.openrouter_base_url == "https://openrouter.ai/api/v1"
        assert c.classifier_model == "z-ai/glm-5.3-flash"
        assert c.max_attempts == 3
        assert c.jev_api_url == "https://api.typesafe.ai/v1/systemone"
        assert (c.jev_model, c.jev_threshold) == ("jev-1.13.0", 0.90)
    finally:
        for name in names:
            os.environ.pop(name, None)


def test_env_example_holds_no_values_for_keys():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*\w*_API_KEY\s*=", line):
            assert line.split("=", 1)[1].strip() == "", line


def _git(*args) -> subprocess.CompletedProcess:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def test_dotenv_is_ignored_and_the_example_is_not():
    assert _git("check-ignore", "-q", ".env").returncode == 0
    assert _git("check-ignore", "-q", ".env.example").returncode == 1


#: Shapes of real credentials: OpenRouter keys, bearer tokens, and any
#: *_API_KEY assignment carrying a value.
_SECRET = re.compile(
    r"sk-or-v1-[0-9a-f]{20,}"
    r"|Bearer\s+[A-Za-z0-9_\-.]{24,}"
    r"|apikey_[0-9a-f]{20,}"
    # [ \t]* rather than \s*: an empty `KEY=` must not run on into the next line.
    r"|^[ \t]*(?:export[ \t]+)?\w*_API_KEY[ \t]*=[ \t]*['\"]?[A-Za-z0-9_\-.]{12,}",
    re.M,
)


def test_secret_pattern_flags_values_but_not_empty_placeholders():
    assert _SECRET.search("OPENROUTER_API_KEY=abcdefghijklmnop1234")
    assert _SECRET.search("sk-or-v1-" + "0" * 40)
    assert not _SECRET.search("OPENROUTER_API_KEY=\nTYPESAFE_API_KEY=\n")


def test_no_secrets_in_committable_files():
    listed = _git("ls-files", "--cached", "--others", "--exclude-standard")
    files = [ROOT / p for p in listed.stdout.splitlines()]
    assert ".env" not in {f.name for f in files}, ".env must never be committable"
    for path in files:
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        match = _SECRET.search(text)
        assert match is None, f"{path.relative_to(ROOT)}: {match.group(0)[:20]}..."
