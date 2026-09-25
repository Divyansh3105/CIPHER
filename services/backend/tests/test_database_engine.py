"""build_engine picks the pooling that is safe for the pooler DATABASE_URL points at.

Getting this backwards is not slow, it is broken: caching prepared statements
on the transaction pooler produced 255 errors in 480 queries when measured.
"""
from app.core.database import build_engine


def test_transaction_pooler_gets_no_statement_caching():
    engine = build_engine("postgresql://u:p@pooler.example.com:6543/postgres")

    assert engine.url.query["prepared_statement_cache_size"] == "0"
    assert engine.pool._pre_ping is False  # costs ~3 round trips with caches off


def test_session_pooler_caches_and_pings():
    engine = build_engine("postgresql://u:p@pooler.example.com:5432/postgres")

    assert "prepared_statement_cache_size" not in engine.url.query
    assert engine.pool._pre_ping is True


def test_both_keep_connections_open():
    for port in (6543, 5432):
        engine = build_engine(f"postgresql://u:p@pooler.example.com:{port}/postgres")
        assert type(engine.pool).__name__ == "AsyncAdaptedQueuePool"  # not NullPool
