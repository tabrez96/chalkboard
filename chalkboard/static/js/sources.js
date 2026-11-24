(function () {
  var form = document.getElementById("upload-form");
  if (!form) return;
  var max = Number(form.dataset.maxBytes);
  var token = form.csrf_token.value;
  var message = document.getElementById("upload-message");
  var progress = document.getElementById("upload-progress");
  var KEY = "chalkboard.uploadMessage";

  function show(text) {
    message.textContent = text;
    message.hidden = false;
  }

  var stored = sessionStorage.getItem(KEY);
  if (stored) {
    show(stored);
    sessionStorage.removeItem(KEY);
  }

  // Batches (picker and drop) run one after another; the page reloads once, after the last one.
  var queue = Promise.resolve();
  var pending = 0;
  var uploaded = false;
  var notes = [];

  // One file per request, so several large files never exceed the request limit.
  async function upload(files) {
    var ok = files.filter(function (f) {
      return f.size <= max;
    });
    files
      .filter(function (f) {
        return f.size > max;
      })
      .forEach(function (f) {
        notes.push(f.name + ": The file is larger than 50 MB");
      });
    for (var i = 0; i < ok.length; i++) {
      progress.textContent = "Uploading " + (i + 1) + " of " + ok.length + "…";
      var body = new FormData();
      body.append("csrf_token", token);
      body.append("file", ok[i]);
      var good = false;
      try {
        var response = await fetch(form.action, {
          method: "POST",
          body: body,
          credentials: "same-origin",
          redirect: "manual", // keep the server's flash message for the reload
        });
        good = response.type === "opaqueredirect" || response.ok;
      } catch (e) {
        good = false;
      }
      if (!good) {
        notes.push(ok[i].name + ": The upload didn't go through. Try again");
        progress.textContent = "";
        continue;
      }
      uploaded = true;
    }
    progress.textContent = "";
  }

  function finish() {
    pending--;
    if (pending > 0) return;
    var text = notes.join(". ");
    var reload = uploaded;
    notes = [];
    uploaded = false;
    if (reload) {
      if (text) sessionStorage.setItem(KEY, text);
      location.reload();
    } else if (text) {
      show(text);
    }
  }

  function send(files) {
    if (!files.length) return;
    pending++;
    queue = queue
      .then(function () {
        return upload(files);
      })
      .then(finish, finish);
  }

  var input = document.getElementById("upload-input");
  input.addEventListener("change", function () {
    send(Array.from(input.files));
  });

  var syllabusInput = document.getElementById("syllabus-input");
  if (syllabusInput) {
    syllabusInput.addEventListener("change", function () {
      var f = syllabusInput.files[0];
      if (!f) return;
      if (pending > 0) {
        syllabusInput.value = "";
        return show("Wait for the upload to finish, then add the syllabus");
      }
      if (f.size > max) return show(f.name + ": The file is larger than 50 MB");
      syllabusInput.form.submit();
    });
  }

  function hasFiles(e) {
    return e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files");
  }
  ["dragenter", "dragover"].forEach(function (name) {
    document.addEventListener(name, function (e) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      document.body.classList.add("is-dropping");
    });
  });
  document.addEventListener("dragleave", function (e) {
    if (e.relatedTarget === null) document.body.classList.remove("is-dropping");
  });
  document.addEventListener("drop", function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    document.body.classList.remove("is-dropping");
    send(Array.from(e.dataTransfer.files));
  });
})();
