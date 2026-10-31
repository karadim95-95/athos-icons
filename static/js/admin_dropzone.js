// Order page in the admin: drop the receipt on the box (or click it) and it is saved and emailed right away.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('input[type="file"][data-dropzone]').forEach((input) => {
    const zone = document.createElement("label");
    const text = document.createElement("span");
    zone.className = "dropzone";
    text.textContent = input.dataset.dropzone;
    input.parentNode.insertBefore(zone, input);
    zone.append(text, input);

    const send = () => {
      if (!input.files.length) return;
      text.textContent = `${input.dataset.saving}: ${input.files[0].name}…`;
      zone.classList.add("busy");
      const stay = document.createElement("input");  // come back to this order after saving
      stay.type = "hidden";
      stay.name = "_continue";
      stay.value = "1";
      input.form.appendChild(stay);
      input.form.submit();
    };
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, () => zone.classList.remove("over")));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      if (event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        send();
      }
    });
    input.addEventListener("change", send);
  });
});
