import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db

PHASE6_COLLECTIONS = {
    "devices_robot",
    "devices_devicecredential",
    "devices_robotstate",
    "devices_robotevent",
    "devices_sensordata",
    "conversations_conversation",
    "conversations_message",
    "conversations_aiinteraction",
    "memory_memory",
    "configuration_configuration",
    "firmware_firmware",
    "firmware_otaupdate",
    "safety_safetyevent",
    "analytics_usagelog",
    "analytics_auditlog",
}


def test_no_pending_migrations_after_phase6():
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert plan == []


def test_all_phase6_collections_exist():
    names = set(connection.database.list_collection_names())
    missing = PHASE6_COLLECTIONS - names
    assert missing == set(), f"missing collections: {sorted(missing)}"


def test_migrations_recorded_for_all_phase6_apps():
    from django.db.migrations.recorder import MigrationRecorder

    recorded = set(
        MigrationRecorder.Migration.objects.filter(
            app__in=[
                "devices",
                "conversations",
                "memory",
                "configuration",
                "firmware",
                "safety",
                "analytics",
            ]
        ).values_list("app", flat=True)
    )
    assert recorded == {
        "devices",
        "conversations",
        "memory",
        "configuration",
        "firmware",
        "safety",
        "analytics",
    }
