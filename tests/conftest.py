from __future__ import annotations

from pathlib import Path

import pytest

from ben.config import PROJECT_ROOT, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        data_dir=tmp_path / "data",
        knowledge_dir=PROJECT_ROOT / "knowledge",
        prompts_dir=PROJECT_ROOT / "prompts",
        embedding_backend="hash",
        telegram_bot_token="123:abc",
        telegram_webhook_secret="tg-secret",
        telegram_allowed_user_ids="111,222",
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="999",
        whatsapp_app_secret="wa-app-secret",
        whatsapp_verify_token="wa-verify",
        whatsapp_allowed_numbers="+971500000001",
    )
