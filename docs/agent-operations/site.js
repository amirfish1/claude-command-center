(() => {
  const year = String(new Date().getFullYear());
  document.querySelectorAll(".js-year").forEach((node) => {
    node.textContent = year;
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target) return;
      const text = target.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch (_) {
        button.textContent = "Select and copy";
      }
    });
  });
})();
