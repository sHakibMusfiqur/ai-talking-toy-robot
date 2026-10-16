/* Shared page behaviour: mobile sidebar toggle, sign out, greeting, and the
   Connect Robot pairing dialog (bound only when the dialog markup is present,
   so pages without it keep loading this file standalone). RobotAPI is touched
   exclusively inside the submit handler, never at load time. */
(function () {
  "use strict";

  var toggle = document.querySelector("[data-nav-toggle]");
  var sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      var open = sidebar.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  document.querySelectorAll("[data-logout]").forEach(function (button) {
    button.addEventListener("click", function () {
      var meta = document.querySelector('meta[name="csrf-token"]');
      var headers = {};
      if (meta) headers["X-CSRFToken"] = meta.getAttribute("content");
      fetch("/api/v1/auth/logout/", {
        method: "POST",
        credentials: "same-origin",
        headers: headers
      }).then(
        function () {
          window.location.assign("/login/");
        },
        function () {
          window.location.assign("/login/");
        }
      );
    });
  });

  /* Account dropdown — present only on pages including the shared header. */
  var accountMenu = document.querySelector("[data-account-menu]");
  if (accountMenu) {
    var accountToggle = accountMenu.querySelector("[data-account-toggle]");
    var accountDropdown = document.getElementById("account-dropdown");
    if (accountToggle && accountDropdown) {
      var setAccountOpen = function (open) {
        accountDropdown.hidden = !open;
        accountToggle.setAttribute("aria-expanded", open ? "true" : "false");
      };
      accountToggle.addEventListener("click", function (event) {
        event.stopPropagation();
        setAccountOpen(accountDropdown.hidden);
      });
      document.addEventListener("click", function (event) {
        if (!accountMenu.contains(event.target)) setAccountOpen(false);
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") setAccountOpen(false);
      });
    }
  }

  /* Time-of-day greeting — purely local presentation, no user data. */
  var greeting = document.querySelector("[data-greeting]");
  if (greeting) {
    var hour = new Date().getHours();
    var phrase =
      hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    greeting.textContent = phrase;
  }

  /* Connect Robot — pairing dialog (dashboard only; no-ops elsewhere). */
  var dialog = document.getElementById("connect-dialog");
  var connectForm = document.getElementById("connect-form");
  if (dialog && connectForm) {
    var codeInput = document.getElementById("connect-code");
    var nicknameInput = document.getElementById("connect-nickname");
    var errorBox = document.getElementById("connect-error");
    var progressBox = document.getElementById("connect-progress");
    var successBox = document.getElementById("connect-success");
    var successName = document.getElementById("connect-success-name");
    var successLink = document.getElementById("connect-success-link");
    var submitButton = connectForm.querySelector('button[type="submit"]');

    
    var requiredElements = [
      ["#connect-code", codeInput],
      ["#connect-nickname", nicknameInput],
      ["#connect-error", errorBox],
      ["#connect-progress", progressBox],
      ["#connect-success", successBox],
      ["#connect-success-name", successName],
      ["#connect-success-link", successLink],
      ['#connect-form button[type="submit"]', submitButton]
    ];
    var missingElements = requiredElements.filter(function (pair) {
      return !pair[1];
    });
    if (missingElements.length) {
      console.error(
        "Connect Robot dialog markup incomplete — missing: " +
          missingElements
            .map(function (pair) {
              return pair[0];
            })
            .join(", ") +
          ". Pairing dialog disabled."
      );
      return;
    }

    var pairRequestActive = false;
    var pairGeneration = 0;

    var showError = function (message) {
      errorBox.textContent = message;
      errorBox.hidden = false;
    };

    var setPairBusy = function (busy) {
      codeInput.disabled = busy;
      nicknameInput.disabled = busy;
      submitButton.disabled = busy;
      submitButton.textContent = busy ? "Pairing\u2026" : "Pair robot";
    };

    /* Full visual reset. Presentation only — never touches pairRequestActive
       (the guard belongs to the in-flight request, not to the dialog). */
    var resetDialog = function () {
      connectForm.hidden = false;
      successBox.hidden = true;
      errorBox.hidden = true;
      errorBox.textContent = "";
      progressBox.hidden = true;
      connectForm.reset();
      setPairBusy(false);
    };

    var closeDialog = function () {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    };

    document.querySelectorAll("[data-connect-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        resetDialog();
        if (pairRequestActive) {
          /* Reopening during a pending request resumes the in-progress
             presentation instead of a fresh submittable form. */
          setPairBusy(true);
          progressBox.hidden = false;
        }
        if (dialog.open) return;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
        if (codeInput && !codeInput.disabled) codeInput.focus();
      });
    });

    document.querySelectorAll("[data-connect-close]").forEach(function (button) {
      button.addEventListener("click", closeDialog);
    });

    document.querySelectorAll("[data-connect-done]").forEach(function (button) {
      button.addEventListener("click", function () {
        window.location.reload();
      });
    });

    /* Backdrop click closes; in-dialog padding clicks must not. */
    dialog.addEventListener("click", function (event) {
      if (event.target !== dialog) return;
      var rect = dialog.getBoundingClientRect();
      var outside =
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom;
      if (outside) closeDialog();
    });

    dialog.addEventListener("close", resetDialog);

    if (codeInput) {
      codeInput.addEventListener("input", function () {
        codeInput.removeAttribute("aria-invalid");
        codeInput.value = codeInput.value
          .toUpperCase()
          .replace(/[^A-Z0-9]/g, "")
          .slice(0, 8);
      });
    }

    connectForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (pairRequestActive) return;
      var code = (codeInput.value || "").trim();
      var nickname = (nicknameInput.value || "").trim().slice(0, 100);

      if (code.length !== 8) {
        codeInput.setAttribute("aria-invalid", "true");
        showError("Enter the 8-character pairing code shown by your robot.");
        codeInput.focus();
        return;
      }
      if (!window.RobotAPI || typeof window.RobotAPI.pair !== "function") {
        showError("This page is missing its API layer. Refresh and try again.");
        return;
      }

      errorBox.hidden = true;
      errorBox.textContent = "";
      progressBox.hidden = true;
      pairRequestActive = true;
      var generation = ++pairGeneration;
      setPairBusy(true);

      /* Final cleanup path for both success and failure: release the guard
         and restore the controls exactly once per request. */
      var finish = function () {
        pairRequestActive = false;
        setPairBusy(false);
        progressBox.hidden = true;
      };

      window.RobotAPI.pair(code, nickname).then(
        function (data) {
          finish();
          if (generation !== pairGeneration) return;
          if (!data || !/^[0-9a-fA-F]{24}$/.test(data.id || "")) {
            showError(
              "Pairing worked, but the robot link was not valid. Refresh the dashboard."
            );
            return;
          }
          successName.textContent =
            data.nickname || data.device_id || "Your robot";
          successLink.setAttribute(
            "href",
            "/dashboard/robots/" + String(data.id).toLowerCase() + "/"
          );
          connectForm.hidden = true;
          successBox.hidden = false;
        },
        function (err) {
          finish();
          if (generation !== pairGeneration) return;
          var status = err && err.status;
          var body = err && err.data;
          if (status === 400 && body && typeof body === "object") {
            codeInput.setAttribute("aria-invalid", "true");
            if (typeof body.detail === "string") {
              showError(body.detail);
              return;
            }
            if (Array.isArray(body.pairing_code) && body.pairing_code.length) {
              showError(String(body.pairing_code[0]));
              return;
            }
          }
          if (status === 403) {
            showError(
              "Your session may have expired. Refresh the page and try again."
            );
            return;
          }
          showError(
            (err && err.message) || window.RobotAPI.friendlyMessage(status)
          );
        }
      );
    });
  }
})();
