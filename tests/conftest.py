import pytest

from pipeline.config import Settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Settings pointing at the repo config but with local storage in a temp dir and no API keys."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SERPER_API_KEY", "S3_BUCKET", "TELEGRAM_BOT_TOKEN", "PUBLIC_BASE_URL", "S3_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CW_SITE_DIR", str(tmp_path / "site"))
    monkeypatch.setenv("CW_OUTPUT_DIR", str(tmp_path / "episodes"))
    return Settings()
