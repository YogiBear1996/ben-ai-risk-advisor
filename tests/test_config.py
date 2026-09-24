from ben.config import DEFAULT_MODEL, PROJECT_ROOT, Settings


def test_env_example_loads_with_empty_values_treated_as_unset(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "BEN_MODEL", "DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=PROJECT_ROOT / ".env.example")
    assert s.ben_model == DEFAULT_MODEL
    assert s.anthropic_api_key is None
    assert s.telegram_bot_token is None
    assert s.vector_dir is None and s.database_url is None
    assert s.telegram_allowed_user_ids == []


def test_csv_lists_and_derived_paths(tmp_path):
    s = Settings(
        _env_file=None,
        data_dir=tmp_path,
        telegram_allowed_user_ids="1, 2",
        whatsapp_allowed_numbers="+971 50 000 0001",
    )
    assert s.telegram_allowed_user_ids == [1, 2]
    assert s.whatsapp_allowed_numbers == ["971500000001"]
    assert s.resolved_database_url == f"sqlite:///{tmp_path / 'ben.db'}"
    assert s.resolved_vector_dir == tmp_path / "lancedb"


def test_model_id_is_only_defined_in_config():
    offenders = [
        p
        for p in (PROJECT_ROOT / "src").rglob("*.py")
        if p.name != "config.py" and "claude-" in p.read_text()
    ]
    assert offenders == []
