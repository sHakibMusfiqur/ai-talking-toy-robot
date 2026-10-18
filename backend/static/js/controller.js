(function () {
  "use strict";

  var main = document.querySelector("main[data-robot-id]");
  if (!main) return;

  var ROBOT_ID = main.getAttribute("data-robot-id");
  var SERVICE_MODE = main.getAttribute("data-service-mode") || "";
  var IS_MOCK = SERVICE_MODE === "mock";

  function $(elementId) {
    return document.getElementById(elementId);
  }

  /* Lets the optional 3D face module (robotFace.js) react to confirmed data.
     Dispatch-only: no rendering behavior changes here, and the face module
     tolerates never being loaded. */
  function emitFaceEvent(kind, detail) {
    document.dispatchEvent(new CustomEvent("robotface:" + kind, { detail: detail }));
  }

  function failServiceUnavailable() {
    var message =
      "Live robot data is unavailable. Please reload the page.";
    ["state", "events", "sensors", "config"].forEach(function (name) {
      var block = $(name + "-block");
      if (block) block.setAttribute("data-state", "error");
      ["loading", "values", "list", "empty"].forEach(function (view) {
        var element = $(name + "-" + view);
        if (element) element.hidden = true;
      });
      var errorView = $(name + "-error");
      if (errorView) errorView.hidden = false;
      var errorText = $(name + "-error-text");
      if (errorText) errorText.textContent = message;
    });
    var status = $("command-status");
    if (status) status.textContent = message;
    document.querySelectorAll("[data-action]").forEach(function (button) {
      button.disabled = true;
    });
  }

  if (IS_MOCK) {
    if (!window.RobotMockService || !window.RobotCommandService) return;
  } else if (!window.RobotAPI) {
    failServiceUnavailable();
    return;
  }

  var API = IS_MOCK ? window.RobotMockService : window.RobotAPI;
  var POLL_MS = 15000;

  function showView(viewMap, activeKey) {
    Object.keys(viewMap).forEach(function (key) {
      viewMap[key].hidden = key !== activeKey;
    });
  }

  function setBlockState(blockId, state) {
    var block = $(blockId);
    if (block) block.setAttribute("data-state", state);
  }

  function errorMessage(error) {
    return error && error.message
      ? error.message
      : "Something went wrong. Please try again.";
  }

  function formatNumber(value, suffix) {
    if (value === null || value === undefined) return "Not available";
    return String(value) + (suffix || "");
  }

  function formatRelative(value) {
    if (!value) return "Not available";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "Not available";
    var seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 0) seconds = 0;
    if (seconds < 5) return "Just now";
    if (seconds < 60) return seconds + " sec ago";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + " h ago";
    return Math.round(hours / 24) + " d ago";
  }

  function formatClock(value) {
    if (!value) return "Not available";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "Not available";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });
  }

  function humanize(value) {
    if (value === null || value === undefined || value === "") return "event";
    return String(value).replace(/[_.\-]+/g, " ");
  }

  var SVG_NS = "http://www.w3.org/2000/svg";

  function svgIcon(paths) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "h-4 w-4 shrink-0");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    paths.forEach(function (d) {
      var path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }

  var SENSOR_ICONS = {
    battery: ["M3 8.5h14v7H3zM21 11v2M6 11.5v1.5"],
    rssi: ["M4 18v-3M9 18v-6M14 18v-9M19 18v-12"],
    signal: ["M4 18v-3M9 18v-6M14 18v-9M19 18v-12"],
    temperature: ["M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z"],
    state: [
      "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
      "M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M5.9 5.9l1.6 1.6M16.5 16.5l1.6 1.6M18.1 5.9l-1.6 1.6M7.5 16.5l-1.6 1.6"
    ],
    motion: ["M3 12h4l2.5-6 4 12L16 12h5"],
    heartbeat: ["M3 12h4l2.5-6 4 12L16 12h5"],
    last_seen: ["M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z", "M12 7.5V12l3 1.8"]
  };
  var SENSOR_ICON_FALLBACK = ["M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"];

  /* Signal meter: 16 vertical bars, level derived from RSSI. */
  var SIGNAL_HEIGHTS = [34, 52, 44, 63, 55, 72, 64, 80, 71, 88, 78, 95, 84, 100, 90, 97];

  function ensureSignalBars(meter) {
    if (!meter) return;
    if (meter.childElementCount === SIGNAL_HEIGHTS.length) return;
    while (meter.firstChild) meter.removeChild(meter.firstChild);
    SIGNAL_HEIGHTS.forEach(function (height) {
      var bar = document.createElement("span");
      bar.className = "sig-bar";
      bar.style.height = height + "%";
      meter.appendChild(bar);
    });
  }

  function signalCount(rssi) {
    if (typeof rssi !== "number" || isNaN(rssi)) return 0;
    if (rssi >= -55) return 16;
    if (rssi >= -62) return 13;
    if (rssi >= -70) return 10;
    if (rssi >= -78) return 7;
    if (rssi >= -85) return 4;
    return 2;
  }

  /* State sparkline: rolling online/offline samples from real polls. */
  var sparkSamples = [];

  function pushSparkSample(isOnline) {
    var line = $("state-spark-line");
    if (!line) return;
    sparkSamples.push(isOnline ? 1 : 0);
    if (sparkSamples.length > 40) sparkSamples.shift();
    var count = sparkSamples.length;
    var points = [];
    if (count === 1) {
      var y0 = sparkSamples[0] ? 8 : 34;
      points.push("0," + y0, "100," + y0);
    } else {
      for (var i = 0; i < count; i++) {
        var x = (i / (count - 1)) * 100;
        var y = sparkSamples[i] ? 8 : 34;
        points.push(x + "," + y);
      }
    }
    line.setAttribute("points", points.join(" "));
  }

  /* ---------- Status ---------- */

  var stateViews = {
    loading: $("state-loading"),
    values: $("state-values"),
    error: $("state-error")
  };
  var stateInFlight = false;

  function renderState(data) {
    if (!data) return;
    var online = data.online === true;
    $("state-dot").classList.toggle("is-online", online);
    $("state-connection").textContent =
      data.online === null || data.online === undefined
        ? "Never reported"
        : online
          ? "Online"
          : "Offline";
    $("state-current").textContent = data.state || "Not available";
    $("state-battery").textContent = formatNumber(data.battery_percent, "%");
    $("state-rssi").textContent = formatNumber(data.rssi, " dBm");
    $("state-last-seen").textContent = formatRelative(data.last_seen);
    /* Offline warning only for a robot that actually reported offline —
       never-reported (online null/undefined) must not claim "offline". */
    $("offline-hint").hidden = data.online !== false;

    var uptime = $("state-uptime");
    if (uptime) uptime.textContent = data.uptime || "Not available";
    var uptimeSub = $("state-uptime-sub");
    if (uptimeSub) {
      uptimeSub.textContent = data.uptime
        ? online
          ? "Online"
          : "Offline"
        : "Not reported by robot yet";
    }

    var pill = $("state-pill");
    if (pill) pill.classList.toggle("is-online", online);
    var wifi = $("identity-wifi");
    if (wifi) wifi.classList.toggle("is-online", online);

    var bar = $("battery-bar");
    if (bar) {
      var battery = data.battery_percent;
      var hasBattery = typeof battery === "number" && !isNaN(battery);
      bar.style.width = (hasBattery ? Math.max(0, Math.min(100, battery)) : 0) + "%";
      bar.classList.toggle("is-low", hasBattery && battery <= 20);
    }

    var meter = $("signal-meter");
    if (meter) {
      ensureSignalBars(meter);
      var lit = signalCount(data.rssi);
      var bars = meter.querySelectorAll(".sig-bar");
      for (var i = 0; i < bars.length; i++) {
        bars[i].classList.toggle("is-on", i < lit);
      }
    }

    var line = $("state-spark-line");
    if (line && data.spark_points) {
      line.setAttribute("points", data.spark_points);
    } else if (data.online === true || data.online === false) {
      pushSparkSample(online);
    }

    setBlockState("state-block", "values");
    showView(stateViews, "values");
    emitFaceEvent("state", {
      online: data.online,
      state: data.state,
      battery: data.battery_percent
    });
  }

  function loadState() {
    if (stateInFlight) return;
    stateInFlight = true;
    setBlockState("state-block", "loading");
    showView(stateViews, "loading");
    API.getState(ROBOT_ID).then(
      function (state) {
        stateInFlight = false;
        renderState(state);
      },
      function (error) {
        stateInFlight = false;
        $("state-error-text").textContent = errorMessage(error);
        setBlockState("state-block", "error");
        showView(stateViews, "error");
      }
    );
  }

  function quietStateRefresh() {
    if (document.hidden || stateInFlight) return;
    API.getState(ROBOT_ID).then(renderState, function () {
      /* Poll failure keeps the last known values on screen; the explicit
         retry path (loadState) covers persistent errors. */
    });
  }

  /* ---------- Events ---------- */

  var eventsViews = {
    loading: $("events-loading"),
    values: $("events-list"),
    empty: $("events-empty"),
    error: $("events-error")
  };

  function eventTone(type) {
    var text = String(type || "").toLowerCase();
    if (text.indexOf("command") !== -1) return "hot";
    if (text.indexOf("state") !== -1) return "info";
    return "ok";
  }

  function renderEvents(events) {
    var list = $("events-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!events || events.length === 0) {
      setBlockState("events-block", "values");
      showView(eventsViews, "empty");
      return;
    }
    events.forEach(function (event) {
      var item = document.createElement("li");
      item.className = "event-item";
      item.setAttribute(
        "data-tone",
        event.tone || eventTone(event.event_type)
      );

      var body = document.createElement("div");
      body.className = "event-body";

      var type = document.createElement("span");
      type.className = "event-type";
      type.textContent = event.title || humanize(event.event_type);
      body.appendChild(type);

      var detail = document.createElement("span");
      detail.className = "event-payload";
      if (event.subtitle) {
        detail.textContent = event.subtitle;
      } else if (event.payload !== null && event.payload !== undefined) {
        detail.textContent =
          typeof event.payload === "string"
            ? event.payload
            : JSON.stringify(event.payload);
      } else {
        detail.textContent = formatRelative(event.timestamp);
      }
      body.appendChild(detail);

      item.appendChild(body);

      var time = document.createElement("time");
      time.className = "event-time";
      time.textContent = formatClock(event.timestamp);
      item.appendChild(time);

      list.appendChild(item);
    });
    setBlockState("events-block", "values");
    showView(eventsViews, "values");
  }

  function showEventsError(error) {
    $("events-error-text").textContent = errorMessage(error);
    setBlockState("events-block", "error");
    showView(eventsViews, "error");
  }

  function loadEvents() {
    setBlockState("events-block", "loading");
    showView(eventsViews, "loading");
    API.getEvents(ROBOT_ID, 20).then(renderEvents, showEventsError);
  }

  /* ---------- Sensors ---------- */

  var sensorsViews = {
    loading: $("sensors-loading"),
    values: $("sensors-list"),
    empty: $("sensors-empty"),
    error: $("sensors-error")
  };

  function renderSensors(readings) {
    var list = $("sensors-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!readings || readings.length === 0) {
      setBlockState("sensors-block", "values");
      showView(sensorsViews, "empty");
      return;
    }
    readings.forEach(function (reading) {
      var card = document.createElement("div");
      card.className = "sensor-card";

      var iconWrap = document.createElement("span");
      iconWrap.className = "grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-raised text-muted";
      iconWrap.setAttribute("aria-hidden", "true");
      var iconKey = String(reading.sensor_type || "").toLowerCase();
      iconWrap.appendChild(svgIcon(SENSOR_ICONS[iconKey] || SENSOR_ICON_FALLBACK));
      card.appendChild(iconWrap);

      var body = document.createElement("div");
      body.className = "sensor-body";

      var type = document.createElement("span");
      type.className = "sensor-type";
      type.textContent = reading.label || humanize(reading.sensor_type || "sensor");
      body.appendChild(type);

      card.appendChild(body);

      var value = document.createElement("span");
      value.className = "sensor-value";
      value.textContent =
        reading.value === null || reading.value === undefined
          ? "Not available"
          : typeof reading.value === "string"
            ? reading.value
            : JSON.stringify(reading.value);
      card.appendChild(value);

      list.appendChild(card);
    });
    setBlockState("sensors-block", "values");
    showView(sensorsViews, "values");
  }

  function showSensorsError(error) {
    $("sensors-error-text").textContent = errorMessage(error);
    setBlockState("sensors-block", "error");
    showView(sensorsViews, "error");
  }

  function loadSensors() {
    setBlockState("sensors-block", "loading");
    showView(sensorsViews, "loading");
    API.getSensors(ROBOT_ID, 20).then(renderSensors, showSensorsError);
  }

  /* ---------- Configuration (read-only) ---------- */

  var configViews = {
    loading: $("config-loading"),
    values: $("config-values"),
    error: $("config-error")
  };

  var CONFIG_ICON_VERSION = [
    "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z",
    "M14 3v5h5"
  ];
  var CONFIG_ICON_GLOBE = [
    "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
    "M3.5 9.5h17M3.5 14.5h17",
    "M12 3c2.4 2.4 3.6 5.4 3.6 9S14.4 18.6 12 21c-2.4-2.4-3.6-5.4-3.6-9S9.6 5.4 12 3z"
  ];
  var CONFIG_ICON_SPEAKER = [
    "M4 9.5h4L13 5v14l-5-4.5H4z",
    "M16.5 8.5a5 5 0 0 1 0 7"
  ];
  var CONFIG_ICON_SUN = [
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    "M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.5 5.5l1.6 1.6M16.9 16.9l1.6 1.6M18.5 5.5l-1.6 1.6M7.1 16.9l-1.6 1.6"
  ];
  var CONFIG_ICON_WIFI = [
    "M2.5 9.5a14.5 14.5 0 0 1 19 0M5.5 12.8a10 10 0 0 1 13 0M8.7 16.1a5.5 5.5 0 0 1 6.6 0M12 19.5h.01"
  ];
  var CONFIG_ICON_GEAR = [
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M5.9 5.9l1.6 1.6M16.5 16.5l1.6 1.6M18.1 5.9l-1.6 1.6M7.5 16.5l-1.6 1.6"
  ];

  var CONFIG_ICONS = {
    "Configuration Version": CONFIG_ICON_VERSION,
    "Language": CONFIG_ICON_GLOBE,
    "Volume": CONFIG_ICON_SPEAKER,
    "LED Brightness": CONFIG_ICON_SUN,
    "Wi-Fi Mode": CONFIG_ICON_WIFI,
    "Behavior Mode": CONFIG_ICON_GEAR
  };

  function appendConfigRow(list, label, valueText) {
    var row = document.createElement("div");
    row.className = "meta-row config-row";
    var iconWrap = document.createElement("span");
    iconWrap.className = "meta-ico";
    iconWrap.setAttribute("aria-hidden", "true");
    iconWrap.appendChild(svgIcon(CONFIG_ICONS[label] || CONFIG_ICON_GEAR));
    row.appendChild(iconWrap);
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    dd.textContent = valueText;
    row.appendChild(dt);
    row.appendChild(dd);
    list.appendChild(row);
  }

  function renderConfiguration(configuration) {
    var list = $("config-values");
    while (list.firstChild) list.removeChild(list.firstChild);
    appendConfigRow(
      list,
      "Configuration Version",
      configuration && configuration.version !== null
        ? String(configuration.version)
        : "Not available"
    );
    var data = configuration ? configuration.data : null;
    if (data && typeof data === "object" && !Array.isArray(data)) {
      Object.keys(data).forEach(function (key) {
        var value = data[key];
        appendConfigRow(
          list,
          key,
          typeof value === "string" ? value : JSON.stringify(value)
        );
      });
    } else {
      appendConfigRow(list, "Settings", "Not available");
    }
    setBlockState("config-block", "values");
    showView(configViews, "values");
  }

  function showConfigError(error) {
    $("config-error-text").textContent = errorMessage(error);
    setBlockState("config-block", "error");
    showView(configViews, "error");
  }

  function loadConfiguration() {
    setBlockState("config-block", "loading");
    showView(configViews, "loading");
    API.getConfiguration(ROBOT_ID).then(renderConfiguration, showConfigError);
  }

  /* ---------- Commands ---------- */

  var statusElement = $("command-status");
  var commandButtons = document.querySelectorAll("[data-action]");
  var commandInFlight = false;

  function setCommandStatus(message, modifier) {
    statusElement.textContent = message;
    statusElement.classList.remove("is-ok", "is-error");
    if (modifier) statusElement.classList.add(modifier);
  }

  function setCommandsDisabled(disabled) {
    commandButtons.forEach(function (button) {
      button.disabled = disabled;
    });
  }

  function clearCommandStates() {
    commandButtons.forEach(function (other) {
      other.classList.remove("is-ok");
      other.classList.remove("is-busy");
    });
  }

  function refreshEventsQuietly() {
    API.getEvents(ROBOT_ID, 20).then(renderEvents, function () {
      /* Keep the last rendered list if a refresh fails. */
    });
  }

  function finishCommandSuccess(button) {
    commandInFlight = false;
    setCommandsDisabled(false);
    button.classList.remove("is-busy");
    button.classList.add("is-ok");
    emitFaceEvent("command", {
      phase: "done",
      action: button.getAttribute("data-action"),
      ok: true
    });
    refreshEventsQuietly();
    quietStateRefresh();
  }

  function finishCommandFailure(button) {
    commandInFlight = false;
    setCommandsDisabled(false);
    button.classList.remove("is-busy");
    emitFaceEvent("command", {
      phase: "done",
      action: button.getAttribute("data-action"),
      ok: false
    });
  }

  function sendCommand(button) {
    if (commandInFlight) return;
    commandInFlight = true;
    setCommandsDisabled(true);
    clearCommandStates();
    button.classList.add("is-busy");
    var label = button.textContent.trim();
    var action = button.getAttribute("data-action");
    emitFaceEvent("command", { phase: "pending", action: action });

    if (IS_MOCK) {
      setCommandStatus("Queuing " + label + "…", null);
      window.RobotCommandService.sendRobotCommand(action).then(
        function () {
          finishCommandSuccess(button);
          setCommandStatus(label + " queued.", "is-ok");
          if (window.RoboToast) {
            window.RoboToast.show(
              "Command " + label.toUpperCase() + " queued",
              "success"
            );
          }
        },
        function (error) {
          finishCommandFailure(button);
          setCommandStatus(errorMessage(error), "is-error");
          if (window.RoboToast) {
            window.RoboToast.show(errorMessage(error), "error");
          }
        }
      );
      return;
    }

    setCommandStatus("Sending " + label + "…", null);
    API.sendCommand(ROBOT_ID, action).then(
      function () {
        finishCommandSuccess(button);
        setCommandStatus(label + " sent.", "is-ok");
        if (window.RoboToast) {
          window.RoboToast.show(
            "Command " + label.toUpperCase() + " sent",
            "success"
          );
        }
      },
      function () {
        finishCommandFailure(button);
        setCommandStatus(label + " could not be sent.", "is-error");
        if (window.RoboToast) {
          window.RoboToast.show("Command could not be sent", "error");
        }
      }
    );
  }

  commandButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      sendCommand(button);
    });
  });

  /* ---------- Wire-up ---------- */

  $("state-retry").addEventListener("click", loadState);
  $("events-retry").addEventListener("click", loadEvents);
  $("sensors-retry").addEventListener("click", loadSensors);
  $("config-retry").addEventListener("click", loadConfiguration);

  API.getSummary(ROBOT_ID).then(
    function (summary) {
      renderState(summary.state);
      renderEvents(summary.events);
      renderConfiguration(summary.configuration);
    },
    function (error) {
      $("state-error-text").textContent = errorMessage(error);
      setBlockState("state-block", "error");
      showView(stateViews, "error");
      showEventsError(error);
      showConfigError(error);
    }
  );
  loadSensors();

  setInterval(quietStateRefresh, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) quietStateRefresh();
  });
})();
