// Product form in the admin: prices incl./excl. VAT kept in step, size rows, one-click discounts.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("product_form");
  if (!form) return;
  const toNumber = (input) => parseFloat(String(input.value).replace(",", "."));
  const show = (n) => (Math.round(n * 100) / 100).toFixed(2);

  // Every "excl. VAT" box names its "incl. VAT" partner; typing in one fills the other.
  form.querySelectorAll("[data-net-of]").forEach((net) => {
    const gross = document.getElementById(net.dataset.netOf);
    const vat = parseFloat(net.dataset.vat);
    if (!gross) return;
    net.addEventListener("input", () => {
      gross.value = net.value === "" ? "" : show(toNumber(net) * vat);
      gross.dispatchEvent(new Event("change"));
    });
    gross.addEventListener("input", () => {
      net.value = gross.value === "" ? "" : show(toNumber(gross) / vat);
    });
  });

  // Ticking a size starts it at the main price.
  const mainPrice = document.getElementById("id_price");
  form.querySelectorAll("[data-size-price]").forEach((box) => {
    box.addEventListener("change", () => {
      const price = document.getElementById(box.dataset.sizePrice);
      if (box.checked && price && price.value === "" && mainPrice.value !== "") {
        price.value = mainPrice.value;
        price.dispatchEvent(new Event("input"));
      }
    });
  });

  // Discount buttons, and what customers pay after the discount.
  const discount = document.getElementById("id_discount_percent");
  const preview = form.querySelector("[data-discount-preview]");
  const updatePreview = () => {
    if (!preview) return;
    const price = toNumber(mainPrice), percent = toNumber(discount) || 0;
    preview.textContent = isNaN(price) || !percent ? "" :
      `${preview.dataset.discountPreview}: ${show(price * (100 - percent) / 100)} €`;
  };
  form.querySelectorAll("[data-discount]").forEach((button) => {
    button.addEventListener("click", () => {
      discount.value = button.dataset.discount;
      updatePreview();
    });
  });
  [discount, mainPrice].forEach((input) => input && ["input", "change"].forEach((e) => input.addEventListener(e, updatePreview)));
  updatePreview();
});
