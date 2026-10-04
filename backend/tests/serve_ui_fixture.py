"""Local UI fixture, no paid LLM calls. Run from root: python backend/tests/serve_ui_fixture.py."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main
from app.pipeline import Pipeline
from app.llm.client import ScriptedClient
from test_analyst import sample_plan

if __name__ == '__main__':
    import uvicorn
    # REQUIRE_LOGIN=true UI_PORT=8095 python backend/tests/serve_ui_fixture.py — the team / login smoke
    os.environ.setdefault('REQUIRE_LOGIN', 'false')
    port = int(os.environ.get('UI_PORT', '8094'))
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
    from app.collab.routes import init_store
    init_store(Path(tempfile.mkdtemp()) / 'app.db')        # fresh users and teams on every start
    uvicorn.run(main.app, host='127.0.0.1', port=port)
