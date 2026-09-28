import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db


def test_no_pending_migrations():
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert plan == []


def test_database_engine_is_mongodb():
    assert connection.settings_dict["ENGINE"] == "django_mongodb_backend"


def test_database_connection_works():
    collections = connection.database.list_collection_names()
    assert isinstance(collections, list)
