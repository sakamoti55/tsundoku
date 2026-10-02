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

document.querySelectorAll("[data-image-input]").forEach((input) => {
  const preview = input.parentElement.querySelector("[data-image-preview]");
  const image = preview?.querySelector("img");
  const fileName = preview?.querySelector("[data-file-name]");
  let previewUrl;

  input.addEventListener("change", () => {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = undefined;
    }

    const file = input.files?.[0];
    if (!file || !preview || !image || !fileName) {
      if (preview) preview.hidden = true;
      return;
    }

    previewUrl = URL.createObjectURL(file);
    image.src = previewUrl;
    fileName.textContent = file.name;
    preview.hidden = false;
  });
});
