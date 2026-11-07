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
