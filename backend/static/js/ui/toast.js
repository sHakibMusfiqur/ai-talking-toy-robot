(function () {
  "use strict";

  function ensureHost() {
    var host = document.getElementById("toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "toast-host";
      host.className = "toast-host";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }

  function show(message, type) {
    var host = ensureHost();

    var toast = document.createElement("div");
    toast.className = "toast" + (type ? " is-" + type : "");

    var dot = document.createElement("span");
    dot.className = "toast-dot";
    dot.setAttribute("aria-hidden", "true");

    var text = document.createElement("span");
    text.className = "toast-message";
    text.textContent = message;

    toast.appendChild(dot);
    toast.appendChild(text);
    host.appendChild(toast);

    window.setTimeout(function () {
      toast.classList.add("is-leaving");
      window.setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 220);
    }, 3200);
  }

  window.RoboToast = { show: show };
})();
