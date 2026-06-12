(function () {
  "use strict";
  const STORAGE_KEY = "wichy-theme";

  function getTheme() {
    return localStorage.getItem(STORAGE_KEY) || "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
  }

  function setTheme(theme) {
    if (theme !== "light" && theme !== "dark") return;
    localStorage.setItem(STORAGE_KEY, theme);
    applyTheme(theme);
    updateIcons();
  }

  function toggleTheme() {
    setTheme(getTheme() === "light" ? "dark" : "light");
  }

  function updateIcons() {
    const theme = document.documentElement.getAttribute("data-theme");
    document.querySelectorAll("[data-theme-icon]").forEach((el) => {
      el.textContent = theme === "dark" ? "\u2600\uFE0F" : "\u263D";
    });
  }

  window.wichyTheme = { get: getTheme, set: setTheme, toggle: toggleTheme };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", toggleTheme);
    });
    updateIcons();
  });
})();
