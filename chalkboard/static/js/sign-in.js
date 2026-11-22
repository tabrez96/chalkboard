const form = document.getElementById("signin-form");
const button = form.querySelector("button[type=submit]");
const label = button.textContent;

form.addEventListener("submit", () => {
  button.disabled = true;
  button.textContent = "Signing in…";
  form.setAttribute("aria-busy", "true");
});

// Coming back with the browser's back button restores the page from cache.
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    button.disabled = false;
    button.textContent = label;
    form.removeAttribute("aria-busy");
  }
});
