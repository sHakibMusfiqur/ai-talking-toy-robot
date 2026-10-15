(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF_TOKEN = meta ? meta.getAttribute("content") : "";

 
  var BYPASS_META = document.querySelector('meta[name="local-ui-bypass"]');

  function friendlyMessage(status) {
    if (!status) return "Network problem — check your connection and try again.";
    if (status === 401) return "Your session has expired. Please sign in again.";
    if (status === 403) return "You do not have permission to do that.";
    if (status === 404) return "Robot not found.";
    if (status === 429) return "Too many requests — please wait a moment and try again.";
    if (status >= 500) return "The server had a problem. Please try again.";
    return "Something went wrong. Please try again.";
  }

  function request(method, path, body) {
    var headers = { Accept: "application/json" };
    var init = { method: method, credentials: "same-origin", headers: headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    if (method !== "GET" && CSRF_TOKEN) {
      headers["X-CSRFToken"] = CSRF_TOKEN;
    }
    return fetch(path, init).then(
      function (response) {
        if (response.status === 401) {
          if (!BYPASS_META) {
            var next = encodeURIComponent(
              window.location.pathname + window.location.search
            );
            window.location.assign("/login/?next=" + next);
          }
          throw { status: 401, message: friendlyMessage(401) };
        }
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (data) {
            if (!response.ok) {
              throw {
                status: response.status,
                message: friendlyMessage(response.status),
                data: data
              };
            }
            return data;
          });
      },
      function () {
        throw { status: 0, message: friendlyMessage(0) };
      }
    );
  }

  function robotUrl(robotId, suffix) {
    return "/api/v1/devices/" + encodeURIComponent(robotId) + "/" + suffix;
  }

  window.RobotAPI = {
    friendlyMessage: friendlyMessage,
    getSummary: function (robotId) {
      return request("GET", robotUrl(robotId, "summary/"));
    },
    getState: function (robotId) {
      return request("GET", robotUrl(robotId, "state/"));
    },
    getEvents: function (robotId, limit) {
      return request(
        "GET",
        robotUrl(robotId, "events/") + "?limit=" + (limit || 20)
      );
    },
    getSensors: function (robotId, limit) {
      return request(
        "GET",
        robotUrl(robotId, "sensors/") + "?limit=" + (limit || 20)
      );
    },
    getConfiguration: function (robotId) {
      return request("GET", robotUrl(robotId, "configuration/"));
    },
    sendCommand: function (robotId, action) {
      return request("POST", robotUrl(robotId, "command/"), { action: action });
    }
  };
})();
