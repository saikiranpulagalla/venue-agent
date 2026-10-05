from pathlib import Path

import pytest

from app import main


def test_process_local_state_rejects_multi_worker_environment(monkeypatch):
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    with pytest.raises(RuntimeError, match='must be 1'):
        main._enforce_single_worker_config()


def test_docker_contract_uses_one_worker_no_access_log_and_real_render_health(project_root: Path):
    dockerfile = (project_root / 'Dockerfile').read_text()
    assert '"--workers","1"' in dockerfile
    assert '"--no-access-log"' in dockerfile
    assert '/health/deep-readiness' in dockerfile


def test_blank_session_root_uses_dedicated_temp_default():
    resolved = main._resolve_session_root('')
    assert resolved.name == 'venue-agent-sessions'
    assert resolved.is_absolute()


def test_relative_or_dangerous_session_roots_are_rejected():
    with pytest.raises(RuntimeError):
        main._resolve_session_root('relative/path')
    with pytest.raises(RuntimeError):
        main._resolve_session_root('/')
