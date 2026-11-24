(() => {
  const panel = document.getElementById("passage-panel");
  let opener = null;
  let requestId = 0;

  const closePanel = () => {
    requestId += 1;
    panel.hidden = true;
    panel.innerHTML = "";
    if (opener) opener.focus();
  };

  if (panel) {
    document.addEventListener("click", async (event) => {
      const link = event.target.closest("a.cite, a.unit-cite");
      if (link) {
        event.preventDefault();
        opener = link;
        const id = ++requestId;
        try {
          const r = await fetch(link.dataset.panel);
          if (!r.ok) throw new Error(r.status);
          const html = await r.text();
          if (id !== requestId) return;
          panel.innerHTML = html;
          panel.hidden = false;
          panel.focus();
        } catch {
          if (id === requestId) location.href = link.href;
        }
        return;
      }
      if (event.target.closest(".passage-close")) {
        event.preventDefault();
        closePanel();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) closePanel();
    });

    const cite = new URL(location.href).searchParams.get("cite");
    if (cite && !panel.hidden) {
      opener =
        [...document.querySelectorAll("a.cite")].find(
          (a) => new URL(a.href).searchParams.get("cite") === cite,
        ) || null;
    }
    const unit = new URL(location.href).searchParams.get("unit");
    if (unit && !panel.hidden) {
      opener =
        [...document.querySelectorAll("a.unit-cite")].find(
          (a) => new URL(a.href).searchParams.get("unit") === unit,
        ) || null;
    }
  }

  const showIt = document.getElementById("show-it");
  if (showIt) {
    showIt.addEventListener("click", (event) => {
      const c = document.querySelector("a.cite.is-flag");
      if (!c) return;
      event.preventDefault();
      c.scrollIntoView({ block: "center" });
      c.focus();
      c.click();
    });
  }

  let progress = document.getElementById("overview-progress");
  if (progress) {
    const timer = setInterval(async () => {
      try {
        const r = await fetch(progress.dataset.progress);
        if (r.status === 204) {
          clearInterval(timer);
          location.reload();
        } else if (r.ok) {
          progress.outerHTML = await r.text();
          progress = document.getElementById("overview-progress");
        }
      } catch {
        // Try again on the next tick.
      }
    }, 2000);
  }
})();
