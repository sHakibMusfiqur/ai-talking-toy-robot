import pytest
from analytics.models import AuditLog, UsageLog
from configuration.models import Configuration
from conversations.models import AIInteraction, Conversation, Message
from devices.models import DeviceCredential, Robot, RobotEvent, RobotState, SensorData
from firmware.models import Firmware, OTAUpdate
from memory.models import Memory
from safety.models import SafetyEvent

from .helpers import new_device_id

pytestmark = pytest.mark.django_db


def test_all_phase6_entities_exist():
    entities = [
        Robot,
        DeviceCredential,
        Conversation,
        Message,
        AIInteraction,
        Memory,
        RobotState,
        RobotEvent,
        Configuration,
        Firmware,
        OTAUpdate,
        SafetyEvent,
        UsageLog,
        SensorData,
        AuditLog,
    ]
    assert len(entities) == 15
    for model in entities:
        assert model._meta.app_label
        assert model._meta.pk.name == "id"


def test_objectid_primary_keys_not_integers():
    for model in (
        Robot,
        DeviceCredential,
        Conversation,
        Message,
        AIInteraction,
        Memory,
        RobotState,
        RobotEvent,
        Configuration,
        Firmware,
        OTAUpdate,
        SafetyEvent,
        UsageLog,
        SensorData,
        AuditLog,
    ):
        from django_mongodb_backend.fields import ObjectIdAutoField

        assert isinstance(model._meta.pk, ObjectIdAutoField)


def test_robot_defaults_are_unpaired():
    robot = Robot.objects.create(device_id=new_device_id())
    assert robot.pairing_status == Robot.PAIRING_UNPAIRED
    assert robot.parent is None
    assert robot.pairing_code_hash == ""
    assert robot.pairing_code_expires_at is None
    assert robot.paired_at is None
    assert robot.nickname == ""


def test_robot_relationships_exist():
    robot = Robot.objects.create(device_id=new_device_id())
    DeviceCredential.objects.create(
        robot=robot, public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----"
    )
    assert robot.credential.robot_id == robot.pk

    RobotState.objects.create(robot=robot, state="IDLE")
    assert robot.state.state == "IDLE"

    event = RobotEvent.objects.create(robot=robot, event_type="booted")
    assert robot.events.count() == 1
    assert event.robot_id == robot.pk

    SensorData.objects.create(robot=robot, sensor_type="battery", value=87.5)
    assert robot.sensor_data.count() == 1

    Configuration.objects.create(robot=robot, version=1, is_current=True)
    assert robot.configurations.count() == 1

    firmware = Firmware.objects.create(version="1.0.0")
    OTAUpdate.objects.create(robot=robot, firmware=firmware)
    assert robot.ota_updates.count() == 1


def test_parent_robot_relationship():
    from accounts.models import Parent
    from accounts.services import create_parent_account

    create_parent_account("a@example.com", "S3cure!Passw0rd")
    parent = Parent.objects.get(user__email="a@example.com")
    robot = Robot.objects.create(device_id=new_device_id(), parent=parent)
    assert robot in parent.robots.all()


def test_conversation_message_aiinteraction_chain():
    robot = Robot.objects.create(device_id=new_device_id())
    conversation = Conversation.objects.create(robot=robot, language="en")
    message = Message.objects.create(
        conversation=conversation, role=Message.ROLE_ROBOT, text="hello"
    )
    AIInteraction.objects.create(message=message, latency_ms=120, emotion="happy")
    assert conversation.messages.count() == 1
    assert message.ai_interaction.emotion == "happy"


def test_memory_links_to_child_profile():
    from accounts.models import ChildProfile, Parent
    from accounts.services import create_parent_account

    create_parent_account("a@example.com", "S3cure!Passw0rd")
    parent = Parent.objects.get(user__email="a@example.com")
    child = ChildProfile.objects.create(
        parent=parent, first_name="Nadi", age_band="7-9", language="en"
    )
    Memory.objects.create(child_profile=child, key="favorite_animal", value="elephant")
    assert child.memories.count() == 1


def test_safety_event_relationships():
    from accounts.models import ChildProfile, Parent
    from accounts.services import create_parent_account

    create_parent_account("a@example.com", "S3cure!Passw0rd")
    parent = Parent.objects.get(user__email="a@example.com")
    child = ChildProfile.objects.create(
        parent=parent, first_name="Nadi", age_band="7-9", language="en"
    )
    robot = Robot.objects.create(device_id=new_device_id())
    conversation = Conversation.objects.create(robot=robot, child_profile=child)
    SafetyEvent.objects.create(
        child_profile=child,
        robot=robot,
        conversation=conversation,
        category="personal_info_request",
        severity="high",
    )
    assert child.safety_events.count() == 1
    assert robot.safety_events.count() == 1
    assert conversation.safety_events.count() == 1


def test_audit_log_actor_relationship():
    from accounts.services import create_parent_account

    user = create_parent_account("a@example.com", "S3cure!Passw0rd")
    AuditLog.objects.create(actor=user, action="robot.paired", target="x")
    assert user.audit_logs.count() == 1


def test_usage_log_defaults():
    from accounts.models import ChildProfile, Parent
    from accounts.services import create_parent_account
    from datetime import date

    create_parent_account("a@example.com", "S3cure!Passw0rd")
    parent = Parent.objects.get(user__email="a@example.com")
    child = ChildProfile.objects.create(
        parent=parent, first_name="Nadi", age_band="7-9", language="en"
    )
    log = UsageLog.objects.create(child_profile=child, date=date.today())
    assert log.minutes_active == 0
    assert log.topics_covered == []
    assert log.quiz_scores == {}
