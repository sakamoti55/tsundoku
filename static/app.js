"use strict";

document.querySelectorAll("form[data-auto-submit]").forEach((form) => {
  const select = form.querySelector("select");

  select?.addEventListener("change", () => {
    form.requestSubmit();
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const message = form.dataset.confirm;

    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });
});
