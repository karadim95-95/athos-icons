// Small enhancements only – every page works without JavaScript.
document.addEventListener("DOMContentLoaded", () => {
  // Mobile menu and search toggles
  document.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.toggle);
      const open = target.classList.toggle("open");
      btn.setAttribute("aria-expanded", open);
      if (open) target.querySelector("input")?.focus();
    });
  });

  // Product gallery: click a thumbnail to show it large
  const main = document.querySelector("[data-gallery-main]");
  document.querySelectorAll("[data-gallery-thumb]").forEach((thumb) => {
    thumb.addEventListener("click", () => {
      main.src = thumb.dataset.full;
      document.querySelectorAll("[data-gallery-thumb]").forEach((t) => t.setAttribute("aria-current", t === thumb));
    });
  });

  // Registration: show company fields only for business accounts
  const typeInputs = document.querySelectorAll('input[name="account_type"]');
  const businessFields = document.querySelector("[data-business-fields]");
  if (typeInputs.length && businessFields) {
    const sync = () => {
      const checked = document.querySelector('input[name="account_type"]:checked');
      businessFields.hidden = !checked || checked.value !== "business";
    };
    typeInputs.forEach((i) => i.addEventListener("change", sync));
    sync();
  }

  // Sort dropdown submits itself
  document.querySelectorAll("[data-autosubmit]").forEach((el) => el.addEventListener("change", () => el.form.submit()));

  // Ask before destructive buttons
  document.querySelectorAll("[data-confirm]").forEach((el) =>
    el.addEventListener("click", (e) => { if (!confirm(el.dataset.confirm)) e.preventDefault(); })
  );
});
