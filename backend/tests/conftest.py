"""
Asahi ERP - Test Configuration
Shared fixtures untuk semua tests
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import BaseModel, get_db
from app.main import app

# Test database URL (separate dari development)
TEST_DATABASE_URL = "postgresql://asahi_dev:asahi_dev_123@localhost:5432/asahi_erp_test"

# Create test engine
test_engine = create_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
)

# Create test session factory
TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


@pytest.fixture(scope="session")
def db_engine():
    """Create test database tables once per test session"""
    BaseModel.metadata.create_all(bind=test_engine)
    yield test_engine
    BaseModel.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def db_session(db_engine):
    """
    Create a fresh database session for each test.
    Rolls back all changes after each test.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    """
    Create test client with database override.
    """

    def override_get_db():
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def api_client(client):
    """
    Alias untuk client fixture.
    """
    return client
