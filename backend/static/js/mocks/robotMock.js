(function () {
  "use strict";

  function isoSecondsAgo(seconds) {
    return new Date(Date.now() - seconds * 1000).toISOString();
  }

  window.RobotMockData = {
    state: {
      online: true,
      state: "Playing",
      battery_percent: 87,
      rssi: -42,
      uptime: "2h 14m",
      temperature: 32.5,
      last_seen: isoSecondsAgo(3),
      last_updated: isoSecondsAgo(3),
      firmware_version: null,
      spark_points:
        "0,26 7,24 14,27 21,19 28,22 35,14 42,18 49,11 56,15 63,9 70,13 77,8 84,12 91,7 100,10"
    },

    events: [
      {
        tone: "hot",
        title: "Command WAVE received",
        subtitle: "A few seconds ago",
        timestamp: isoSecondsAgo(2)
      },
      {
        tone: "ok",
        title: "Robot status updated",
        subtitle: "Battery 87% \u2022 RSSI -42 dBm",
        timestamp: isoSecondsAgo(7)
      },
      {
        tone: "ok",
        title: "Heartbeat received",
        subtitle: "System online",
        timestamp: isoSecondsAgo(19)
      },
      {
        tone: "info",
        title: "State changed to playing",
        subtitle: "Robot is active",
        timestamp: isoSecondsAgo(45)
      },
      {
        tone: "ok",
        title: "Connected to MQTT",
        subtitle: "Device online",
        timestamp: isoSecondsAgo(76)
      }
    ],

    sensors: [
      { sensor_type: "battery", label: "Battery", value: "87%", timestamp: isoSecondsAgo(3) },
      { sensor_type: "rssi", label: "RSSI", value: "-42 dBm", timestamp: isoSecondsAgo(3) },
      {
        sensor_type: "temperature",
        label: "Temperature",
        value: "32.5 \u00B0C",
        timestamp: isoSecondsAgo(12)
      },
      { sensor_type: "state", label: "State", value: "playing", timestamp: isoSecondsAgo(3) },
      { sensor_type: "motion", label: "Motion", value: "idle", timestamp: isoSecondsAgo(30) },
      {
        sensor_type: "last_seen",
        label: "Last Seen",
        value: "Just now",
        timestamp: isoSecondsAgo(3)
      }
    ],

    configuration: {
      version: "v12",
      data: {
        "Language": "English / Bangla",
        "Volume": "70%",
        "LED Brightness": "60%",
        "Wi-Fi Mode": "Station",
        "Behavior Mode": "Standard"
      },
      is_current: true
    }
  };
})();
