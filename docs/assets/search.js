"use strict";
(() => {
  const search = document.querySelector("#search");
  const model = document.querySelector("#model");
  const bin = document.querySelector("#bin");
  const rows = [...document.querySelectorAll(".parts li")];
  if (!search || !model || !bin) return;
  document.querySelector(".filters").hidden = false;
  const normal = value => value.toLowerCase().replace(/[^a-z0-9]/g, "");
  function filter() {
    const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let count = 0;
    for (const row of rows) {
      const matchText = terms.every(term => row.dataset.search.includes(term)) ||
        normal(row.dataset.search).includes(normal(search.value));
      row.hidden = !(matchText && (!model.value || row.dataset.models.split(",").includes(model.value)) &&
        (!bin.value || row.dataset.bin === bin.value));
      if (!row.hidden) count++;
    }
    document.querySelector("#results").textContent = `${count} ${count === 1 ? "part" : "parts"}`;
    document.querySelector("#empty").hidden = count !== 0;
  }
  [search, model, bin].forEach(control => control.addEventListener("input", filter));
  filter();
})();
