import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.llm import route_model


def test_manual_mode_keeps_selected_model():
    s = {"model": "nvidia:primary", "model_mode": "manual", "model_roles": {"coding": "nvidia:coder"}}
    assert route_model(s, "implement a parser") == "nvidia:primary"


def test_auto_routes_coding_to_coding_specialist():
    s = {"model": "nvidia:primary", "model_mode": "auto", "model_roles": {"coding": "nvidia:coder"}}
    assert route_model(s, "Implement a parser and add tests") == "nvidia:coder"


def test_auto_routes_reasoning_to_reasoning_specialist():
    s = {"model": "nvidia:primary", "model_mode": "auto", "model_roles": {"reasoning": "nvidia:thinker"}}
    assert route_model(s, "Analyze the architecture and compare trade-offs") == "nvidia:thinker"


def test_hybrid_falls_back_to_primary_for_unmatched_work():
    s = {"model": "nvidia:primary", "model_mode": "hybrid", "model_roles": {"coding": "nvidia:coder"}}
    assert route_model(s, "Tell me a joke") == "nvidia:primary"


def test_missing_role_falls_back_to_primary():
    s = {"model": "nvidia:primary", "model_mode": "auto", "model_roles": {}}
    assert route_model(s, "Debug this Python code") == "nvidia:primary"


def test_manual_mode_can_still_explicitly_use_kimi():
    s = {"model": "kimi:k3.1-pro", "model_mode": "manual", "model_roles": {}}
    assert route_model(s, "implement a parser") == "kimi:k3.1-pro"


def test_auto_mode_ignores_kimi_specialist():
    s = {"model": "nvidia:primary", "model_mode": "auto", "model_roles": {"coding": "kimi:k3.1-pro"}}
    assert route_model(s, "Implement a parser") == "nvidia:primary"


def test_auto_mode_replaces_kimi_primary_with_free_default(monkeypatch):
    from app import llm, config
    monkeypatch.setattr(config, "DEFAULT_MODEL", "")
    old = llm._models_cache.copy()
    llm._models_cache["v"] = [
        {"ref": "kimi:k3.1-pro", "provider": "kimi", "id": "kimi-k3.1-pro"},
        {"ref": "nvidia:nvidia/nemotron-3-ultra", "provider": "nvidia", "id": "nvidia/nemotron-3-ultra"},
    ]
    try:
        s = {"model": "kimi:k3.1-pro", "model_mode": "auto", "model_roles": {}}
        assert route_model(s, "Tell me a joke") == "nvidia:nvidia/nemotron-3-ultra"
    finally:
        llm._models_cache.clear()
        llm._models_cache.update(old)


def test_default_and_fallback_never_choose_kimi(monkeypatch):
    from app import llm, config
    monkeypatch.setattr(config, "DEFAULT_MODEL", "kimi:k3.1-pro")
    monkeypatch.setattr(config, "FALLBACK_MODEL", "kimi:k3.1-pro")
    models = [
        {"ref": "kimi:k3.1-pro", "provider": "kimi", "id": "kimi-k3.1-pro"},
        {"ref": "nvidia:nvidia/nemotron-3-ultra", "provider": "nvidia", "id": "nvidia/nemotron-3-ultra"},
    ]
    assert llm.select_default_model(models) == "nvidia:nvidia/nemotron-3-ultra"
    assert llm.select_fallback_model(models) == "nvidia:nvidia/nemotron-3-ultra"
