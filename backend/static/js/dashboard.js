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

  /* Time-of-day greeting — purely local presentation, no user data. */
  var greeting = document.querySelector("[data-greeting]");
  if (greeting) {
    var hour = new Date().getHours();
    var phrase =
      hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    greeting.textContent = phrase;
  }
})();
