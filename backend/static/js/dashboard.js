/* Shared page behaviour: mobile sidebar toggle, sign out, greeting.
   Self-contained (no RobotAPI dependency) so every page can load it. */
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
})();
