(function () {
  "use strict";

  var confirmSection = document.getElementById("claim-confirm");
  if (!confirmSection) return; // instructions / link-error steps: no claim UI

  var submitButton = document.getElementById("claim-submit");
  var errorBox = document.getElementById("claim-error");
  var nicknameInput = document.getElementById("claim-nickname");
  var successSection = document.getElementById("claim-success");
  var successName = document.getElementById("claim-success-name");

  if (!submitButton || !errorBox) {
    console.error("Onboarding claim markup incomplete");
    return;
  }

  var ticket = confirmSection.getAttribute("data-claim-ticket") || "";
  var claimBusy = false; // single-flight guard: one redeem POST at a time

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  }

  function finish() {
    claimBusy = false;
    submitButton.disabled = false;
    submitButton.textContent = "Add robot";
  }

  submitButton.addEventListener("click", function () {
    if (claimBusy || !ticket) return;
    claimBusy = true;
    submitButton.disabled = true;
    submitButton.textContent = "Adding robot…";
    errorBox.classList.add("hidden");
    errorBox.textContent = "";

    window.RobotAPI.claimRobot(ticket, nicknameInput ? nicknameInput.value : "").then(
      function (robot) {
        finish();
        // The raw ticket has been consumed — drop it from the address bar
        // and memory immediately; it is never written to any web storage.
        ticket = "";
        try {
          window.history.replaceState({}, "", "/onboarding/claim/");
        } catch (e) {
          /* storage of history is best-effort */
        }
        confirmSection.classList.add("hidden");
        if (successName && robot && (robot.nickname || robot.device_id)) {
          successName.textContent = robot.nickname || robot.device_id;
        }
        if (successSection) {
          successSection.classList.remove("hidden");
          successSection.focus();
        }
      },
      function (err) {
        finish();
        var body = (err && err.data) || {};
        if (body.detail) {
          showError(body.detail);
        } else {
          showError((err && err.message) || "Something went wrong. Please try again.");
        }
      }
    );
  });
})();
