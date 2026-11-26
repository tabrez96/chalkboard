document.addEventListener("click", function (event) {
  var button = event.target.closest(".assistant [data-scope]");
  if (!button || button.disabled) return;
  var section = button.closest(".assistant");
  section.querySelectorAll("[data-scope]").forEach(function (b) {
    b.setAttribute("aria-pressed", b === button ? "true" : "false");
  });
  section.querySelector("input[name='scope']").value = button.dataset.scope;
});
