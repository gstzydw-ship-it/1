from app.config import get_config
from app.db.engine import build_engine
from app.db.session import create_session_factory


def test_engine_and_session_factory_can_initialize() -> None:
    config = get_config()
    engine = build_engine(config.database_url)
    factory = create_session_factory(engine)
    session = factory()

    assert str(engine.url).startswith("sqlite:///")
    assert session is not None
    session.close()
