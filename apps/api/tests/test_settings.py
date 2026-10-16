import pytest
from hoy_api.settings import Settings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOY_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/hoy")
    monkeypatch.setenv("HOY_SITE_URL", "https://hoyencanarias.es")
    monkeypatch.setenv("HOY_ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("HOY_CORS_ORIGINS", "https://hoyencanarias.es,http://localhost:3000")

    settings = Settings()

    assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:p@db:5432/hoy"
    assert settings.site_url == "https://hoyencanarias.es"
    assert settings.admin_token is not None
    assert settings.admin_token.get_secret_value() == "secret-token"
    assert settings.cors_origins == ["https://hoyencanarias.es", "http://localhost:3000"]


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOY_DATABASE_URL", "postgresql+psycopg://u:hunter2@db:5432/hoy")
    monkeypatch.setenv("HOY_ADMIN_TOKEN", "secret-token")

    rendered = repr(Settings())

    assert "hunter2" not in rendered
    assert "secret-token" not in rendered


def test_admin_token_is_disabled_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOY_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("HOY_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/hoy")

    assert Settings().admin_token is None


def test_admin_token_rejects_blank_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOY_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/hoy")
    monkeypatch.setenv("HOY_ADMIN_TOKEN", "   ")

    with pytest.raises(ValueError):
        Settings()
