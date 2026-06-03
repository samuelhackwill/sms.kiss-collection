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
