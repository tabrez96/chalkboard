(function () {
  var dialog = document.querySelector(".modal");
  if (!dialog) return;

  // Keep keyboard focus in the dialog: make everything outside it inert.
  var node = dialog.closest(".modal-backdrop");
  while (node && node !== document.body) {
    Array.prototype.forEach.call(node.parentNode.children, function (sibling) {
      if (sibling !== node) sibling.inert = true;
    });
    node = node.parentNode;
  }
})();
