from __future__ import annotations

from ia_kissing_pipeline.config import load_settings


def test_load_settings_reads_dotenv_without_overriding_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APP_ENV=dotenv-env",
                "ROBOFLOW_API_KEY=dotenv-key",
                "ROBOFLOW_WORKFLOW_ID=dotenv-workflow",
            ]
        )
    )
    monkeypatch.setenv("APP_ENV", "real-env")

    settings = load_settings()

    assert settings.app_env == "real-env"
    assert settings.roboflow_api_key == "dotenv-key"
    assert settings.roboflow_workflow_id == "dotenv-workflow"
    assert settings.media_delete_local_after_upload is True
    assert settings.cache_max_bytes == 10 * 1024 * 1024 * 1024
    assert settings.cache_max_age_seconds == 14 * 24 * 60 * 60


def test_load_settings_reads_cleanup_limits(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEDIA_DELETE_LOCAL_AFTER_UPLOAD", "0")
    monkeypatch.setenv("CACHE_MAX_BYTES", "123")
    monkeypatch.setenv("CACHE_MAX_AGE_SECONDS", "456")

    settings = load_settings()

    assert settings.media_delete_local_after_upload is False
    assert settings.cache_max_bytes == 123
    assert settings.cache_max_age_seconds == 456
