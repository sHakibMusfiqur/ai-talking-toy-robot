import time

from django.core.management.base import BaseCommand

from mqtt_bridge.client import MqttError
from mqtt_bridge.subscriber import start_bridge


class Command(BaseCommand):
    help = "Run the Django MQTT bridge (subscriber) with bounded reconnect."

    def handle(self, *args, **options):
        self.stdout.write("starting mqtt bridge...")
        try:
            subscriber = start_bridge()
        except MqttError as exc:
            raise SystemExit(f"mqtt bridge startup failed: {exc}")
        self.stdout.write(
            self.style.SUCCESS(
                f"mqtt bridge connected (client={subscriber.connection.client_id})"
            )
        )
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            self.stdout.write("stopping mqtt bridge...")
        finally:
            subscriber.connection.disconnect()
