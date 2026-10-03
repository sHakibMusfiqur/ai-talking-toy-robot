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

  var logoutButton = document.querySelector("[data-logout]");
  if (logoutButton) {
    logoutButton.addEventListener("click", function () {
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
  }
})();
