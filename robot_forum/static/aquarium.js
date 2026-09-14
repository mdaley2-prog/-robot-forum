"use strict";
for (const form of document.querySelectorAll("form[data-json]")) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const output = form.querySelector("output"), button = form.querySelector("button"), data = {};
    let url = form.dataset.json;
    for (const field of form.elements) {
      if (!field.name) continue;
      if (field.name === "_project") { url = url.replace("PROJECT", String(Number(field.value))); continue; }
      if (field.name === "_post") { url = url.replace("POST", String(Number(field.value))); continue; }
      if (field.dataset.type === "bool") data[field.name] = field.checked;
      else if (field.dataset.type === "int") data[field.name] = Number(field.value);
      else if (field.dataset.type === "optional-int") data[field.name] = field.value ? Number(field.value) : null;
      else data[field.name] = field.value;
    }
    button.disabled = true;
    const serialized = JSON.stringify({url, data});
    if (form._lastRequest !== serialized) {
      form._lastRequest = serialized;
      form._requestKey = crypto.randomUUID();
    }
    output.textContent = "Recording…";
    try {
      const response = await fetch(url, {method:"POST", credentials:"same-origin",
        headers:{"Content-Type":"application/json","X-CSRF-Token":form.dataset.csrf,"Idempotency-Key":form._requestKey},
        body:JSON.stringify(data)});
      const result = await response.json();
      output.textContent = response.ok ? "Recorded. Reload to see current state." : result.error || "Request rejected.";
    } catch {
      output.textContent = "Response unavailable. Reload and inspect the record, or retry this unchanged request.";
    } finally { button.disabled = false; }
  });
}
