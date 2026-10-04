"""Local UI fixture, no paid LLM calls. Run from root: python backend/tests/serve_ui_fixture.py."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main
from app.pipeline import Pipeline
from app.llm.client import ScriptedClient
from test_analyst import sample_plan

if __name__ == '__main__':
    import uvicorn
    first = sample_plan()
    second = sample_plan()
    second['questions'] = []
    pipeline = Pipeline(ScriptedClient([json.dumps(first), json.dumps(second)]))
    main._pipeline = lambda: pipeline
    from app import tools_api
    tools_api._pipeline = main._pipeline
    # No environment/configuration access is required for this fixture.
    from app.config import Settings
    main._state['settings'] = Settings()
    uvicorn.run(main.app, host='127.0.0.1', port=8094)
