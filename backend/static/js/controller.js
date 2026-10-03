(function () {
  "use strict";

  var main = document.querySelector("main[data-robot-id]");
  if (!main || !window.RobotAPI) return;

  var ROBOT_ID = main.getAttribute("data-robot-id");
  var API = window.RobotAPI;
  var POLL_MS = 15000;

  function $(elementId) {
    return document.getElementById(elementId);
  }

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
    if (value === null || value === undefined) return "N/A";
    return String(value) + (suffix || "");
  }

  function formatTime(value) {
    if (!value) return "N/A";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "N/A";
    return date.toLocaleString();
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
    $("state-current").textContent = data.state || "N/A";
    $("state-battery").textContent = formatNumber(data.battery_percent, "%");
    $("state-rssi").textContent = formatNumber(data.rssi, " dBm");
    $("state-last-seen").textContent = formatTime(data.last_seen);
    $("offline-hint").hidden = online;
    setBlockState("state-block", "values");
    showView(stateViews, "values");
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

      var type = document.createElement("span");
      type.className = "event-type mono";
      type.textContent = event.event_type || "event";
      item.appendChild(type);

      var time = document.createElement("time");
      time.className = "event-time";
      time.textContent = formatTime(event.timestamp);
      item.appendChild(time);

      if (event.payload !== null && event.payload !== undefined) {
        var payload = document.createElement("span");
        payload.className = "event-payload mono";
        payload.textContent =
          typeof event.payload === "string"
            ? event.payload
            : JSON.stringify(event.payload);
        item.appendChild(payload);
      }
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

      var type = document.createElement("span");
      type.className = "sensor-type";
      type.textContent = reading.sensor_type || "sensor";
      card.appendChild(type);

      var value = document.createElement("span");
      value.className = "sensor-value";
      value.textContent =
        reading.value === null || reading.value === undefined
          ? "N/A"
          : typeof reading.value === "string"
            ? reading.value
            : JSON.stringify(reading.value);
      card.appendChild(value);

      var time = document.createElement("span");
      time.className = "sensor-time";
      time.textContent = formatTime(reading.timestamp);
      card.appendChild(time);

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

  function appendConfigRow(list, label, valueText) {
    var row = document.createElement("div");
    row.className = "meta-row";
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    dd.className = "mono";
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
      "Version",
      configuration && configuration.version !== null
        ? String(configuration.version)
        : "N/A"
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
      appendConfigRow(list, "Settings", "N/A");
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

  function sendCommand(button) {
    if (commandInFlight) return;
    commandInFlight = true;
    setCommandsDisabled(true);
    commandButtons.forEach(function (other) {
      other.classList.remove("is-ok");
      other.classList.remove("is-busy");
    });
    button.classList.add("is-busy");
    var label = button.textContent.trim();
    setCommandStatus("Sending " + label + "…", null);
    API.sendCommand(ROBOT_ID, button.getAttribute("data-action")).then(
      function () {
        commandInFlight = false;
        setCommandsDisabled(false);
        button.classList.remove("is-busy");
        button.classList.add("is-ok");
        setCommandStatus(label + " sent.", "is-ok");
        quietStateRefresh();
      },
      function (error) {
        commandInFlight = false;
        setCommandsDisabled(false);
        button.classList.remove("is-busy");
        setCommandStatus(errorMessage(error), "is-error");
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
