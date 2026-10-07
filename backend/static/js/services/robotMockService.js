(function () {
  "use strict";

  var LATENCY_MS = 220;

  function mockData() {
    return window.RobotMockData || {};
  }

  function later(value, ms) {
    return new Promise(function (resolve) {
      window.setTimeout(function () {
        resolve(value);
      }, ms === undefined ? LATENCY_MS : ms);
    });
  }

  window.RobotMockService = {
    getState: function (robotId) {
      return later(mockData().state);
    },

    getEvents: function (robotId, limit) {
      var events = mockData().events || [];
      return later(events.slice(0, limit || 20));
    },

    getSensors: function (robotId, limit) {
      var sensors = mockData().sensors || [];
      return later(sensors.slice(0, limit || 20));
    },

    getConfiguration: function (robotId) {
      return later(mockData().configuration);
    },

    getSummary: function (robotId) {
      var self = this;
      return Promise.all([
        self.getState(robotId),
        self.getEvents(robotId, 20),
        self.getConfiguration(robotId)
      ]).then(function (parts) {
        return {
          robot: null,
          state: parts[0],
          events: parts[1],
          configuration: parts[2]
        };
      });
    },

    /* Internal buffer used by the command service (mock queue). */
    prependEvent: function (event) {
      var events = mockData().events;
      if (events && typeof events.unshift === "function") {
        events.unshift(event);
      }
      return event;
    }
  };
})();
