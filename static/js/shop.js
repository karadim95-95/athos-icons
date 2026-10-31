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

  // Product page: the price of the size and options the customer picks (all prices come from the shop).
  const configurator = document.querySelector("[data-configurator]");
  const priceTable = document.getElementById("price-table");
  if (configurator && priceTable) {
    const prices = JSON.parse(priceTable.textContent);
    const keyOf = (size, options) => `${size}:${Object.values(options).sort().join(".")}`;
    const update = () => {
      const size = configurator.querySelector('input[name="size"]:checked')?.value || "";
      const options = {};
      configurator.querySelectorAll('input[name^="option_"]:checked').forEach((i) => { options[i.name] = i.value; });
      const now = prices[keyOf(size, options)];
      if (!now) return;
      const final = document.querySelector("[data-price-final]");
      const original = document.querySelector("[data-price-original]");
      if (final) final.textContent = now.final;
      if (original) original.textContent = now.original;

      const summary = document.querySelector("[data-summary]");
      if (summary) summary.textContent = [...configurator.querySelectorAll("input:checked")].map((i) => i.dataset.label).join(" · ");
    };
    configurator.addEventListener("change", update);
    update();
  }

  // Sort dropdown submits itself
  document.querySelectorAll("[data-autosubmit]").forEach((el) => el.addEventListener("change", () => el.form.submit()));

  // Ask before destructive buttons
  document.querySelectorAll("[data-confirm]").forEach((el) =>
    el.addEventListener("click", (e) => { if (!confirm(el.dataset.confirm)) e.preventDefault(); })
  );
});
