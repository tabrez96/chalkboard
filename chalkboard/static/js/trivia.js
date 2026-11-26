function copyWithTextarea(text) {
  var area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  var ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (error) {
    ok = false;
  }
  document.body.removeChild(area);
  return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(function () {
      return copyWithTextarea(text);
    });
  }
  return copyWithTextarea(text);
}

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  copyText(button.dataset.copy).then(
    function () {
      button.textContent = "Copied";
      setTimeout(function () {
        button.textContent = "Copy for the board";
      }, 2000);
    },
    function () {
      button.textContent = "Couldn't copy";
      setTimeout(function () {
        button.textContent = "Copy for the board";
      }, 2000);
    },
  );
});
