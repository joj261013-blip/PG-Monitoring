"""설정 로딩 및 비밀정보 보호에 대한 기본 테스트."""

import subprocess
from pathlib import Path

import pytest

from pg_monitoring.config import PROJECT_ROOT, load_settings

ENV_VARS = ("NARA_API_KEY", "G2B_SERVICE_KEY", "PG_KEYWORDS", "DATA_DIR", "LOG_LEVEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env_file(tmp_path):
    s = load_settings(env_file=tmp_path / "없는파일.env")
    assert s.has_service_key is False
    assert s.masked_service_key == "(미설정)"
    assert "PG" in s.keywords
    assert s.log_level == "INFO"
    assert s.data_dir == PROJECT_ROOT / "data"


def test_reads_env_file(tmp_path):
    env = tmp_path / "test.env"
    env.write_text(
        "NARA_API_KEY=dummy-test-key-1234\n"
        "PG_KEYWORDS= PG , 결제대행 ,,\n"
        "LOG_LEVEL=debug\n",
        encoding="utf-8",
    )
    s = load_settings(env_file=env)
    assert s.has_service_key
    assert s.keywords == ("PG", "결제대행")
    assert s.log_level == "DEBUG"


def test_environment_variable_overrides_env_file(tmp_path, monkeypatch):
    env = tmp_path / "test.env"
    env.write_text("NARA_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("NARA_API_KEY", "from-secrets")
    assert load_settings(env_file=env).service_key == "from-secrets"


def test_service_key_is_never_printed(tmp_path, monkeypatch):
    monkeypatch.setenv("NARA_API_KEY", "dummy-secret-value-9999")
    s = load_settings(env_file=tmp_path / "none.env")
    assert "dummy-secret-value-9999" not in repr(s)
    # 키의 일부(앞 4자리 등)도 노출되면 안 된다
    assert "dumm" not in s.masked_service_key


def test_legacy_env_name_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "legacy-key")
    assert load_settings(env_file=tmp_path / "none.env").service_key == "legacy-key"


@pytest.mark.parametrize("path", [".env", ".env.local", "secrets/key.txt", "data/result.json"])
def test_secret_files_are_gitignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT, check=False
    )
    assert result.returncode == 0, f"{path} 가 .gitignore 에 포함되어야 합니다"


def test_env_example_is_not_ignored_and_has_no_real_key():
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=PROJECT_ROOT, check=False
    )
    assert result.returncode == 1, ".env.example 은 GitHub에 올라가야 합니다"
    text = Path(PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NARA_API_KEY=\n" in text, "예시 파일의 인증키 값은 비워두어야 합니다"
