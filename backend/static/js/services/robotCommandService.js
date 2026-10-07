(function () {
  "use strict";

  var QUEUE_DELAY_MS = 650;

  function readableAction(action) {
    return String(action || "").replace(/_/g, " ");
  }

  window.RobotCommandService = {
    sendRobotCommand: function (action) {
      return new Promise(function (resolve, reject) {
        if (!action) {
          reject(new Error("Missing command action."));
          return;
        }
        window.setTimeout(function () {
          try {
            if (window.RobotMockService && window.RobotMockService.prependEvent) {
              window.RobotMockService.prependEvent({
                tone: "hot",
                title: "Command " + readableAction(action) + " received",
                subtitle: "A few seconds ago",
                timestamp: new Date().toISOString()
              });
            }
            resolve({ action: action, queued: true });
          } catch (error) {
            reject(error);
          }
        }, QUEUE_DELAY_MS);
      });
    }
  };
})();
