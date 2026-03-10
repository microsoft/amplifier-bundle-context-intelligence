"""Tests for neo4j driver dependency (task-02)."""


def test_neo4j_imports_and_has_version():
    """neo4j package must be importable and expose a version string."""
    import neo4j

    assert hasattr(neo4j, "__version__")
    assert isinstance(neo4j.__version__, str)
    assert len(neo4j.__version__) > 0


def test_async_graph_database_importable():
    """AsyncGraphDatabase must be importable from neo4j for async usage."""
    from neo4j import AsyncGraphDatabase

    assert AsyncGraphDatabase is not None
