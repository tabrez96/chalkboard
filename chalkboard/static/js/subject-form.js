(function () {
  var form = document.querySelector(".modal form");
  if (!form) return;

  var chip = form.querySelector("[data-budget]");

  function chosenDays() {
    var days = {};
    // Like the server's form_times(): a day without a start time doesn't count.
    form.querySelectorAll(".day[aria-pressed='true']").forEach(function (b) {
      var i = Number(b.dataset.day);
      if (form.elements["time_" + i].value) days[i] = true;
    });
    return days;
  }

  function parseDate(value) {
    if (!value) return null;
    var p = value.split("-");
    return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }

  function duration(minutes) {
    var h = Math.floor(minutes / 60);
    var m = minutes % 60;
    if (!h) return m + " min";
    return m ? h + " h " + m + " min" : h + " h";
  }

  // Same rules as timetable.format_budget on the server.
  function updateChip() {
    var days = chosenDays();
    var start = parseDate(form.elements.term_start.value);
    var end = parseDate(form.elements.term_end.value);
    if (!Object.keys(days).length || !start || !end) {
      chip.hidden = true;
      return;
    }
    var count = 0;
    for (
      var d = start;
      d <= end;
      d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)
    ) {
      if (days[(d.getDay() + 6) % 7]) count++;
    }
    var length = parseInt(form.elements.class_minutes.value, 10) || 0;
    chip.textContent = count
      ? count +
        (count === 1 ? " class" : " classes") +
        " · " +
        duration(count * length)
      : "No classes in this term";
    chip.hidden = false;
  }

  form.querySelectorAll(".day").forEach(function (button) {
    button.addEventListener("click", function () {
      var on = button.getAttribute("aria-pressed") !== "true";
      var i = button.dataset.day;
      button.setAttribute("aria-pressed", on ? "true" : "false");
      form.querySelector("input[name='days'][value='" + i + "']").checked = on;
      form.querySelector("[data-time-for='" + i + "']").hidden = !on;
      updateChip();
    });
  });

  var field = form.querySelector("[data-syllabus]");
  var file = form.elements.syllabus_file;
  var choice = form.elements.syllabus;
  var picked = field.querySelector("[data-syllabus-picked]");
  var empty = field.querySelector("[data-syllabus-empty]");
  var nameEl = field.querySelector("[data-syllabus-name]");
  var errorEl = field.querySelector("[data-syllabus-error]");
  var choose = field.querySelector("[data-syllabus-choose]");

  function showPicked(name) {
    nameEl.textContent = name;
    picked.hidden = false;
    empty.hidden = true;
    errorEl.hidden = true;
  }

  function clearPick() {
    choice.value = "remove";
    file.value = "";
    if (choose) choose.value = "";
    picked.hidden = true;
    empty.hidden = false;
  }

  file.addEventListener("change", function () {
    var f = file.files[0];
    if (!f) return;
    if (f.size > Number(file.dataset.maxBytes)) {
      file.value = "";
      errorEl.textContent = f.name + ": The file is larger than 50 MB";
      errorEl.hidden = false;
      return;
    }
    choice.value = "";
    if (choose) choose.value = "";
    showPicked(f.name);
  });

  if (choose) {
    choose.addEventListener("change", function () {
      if (!choose.value) return;
      choice.value = choose.value;
      file.value = "";
      showPicked(choose.selectedOptions[0].dataset.name);
    });
  }

  field
    .querySelector("[data-syllabus-remove]")
    .addEventListener("click", clearPick);
  field
    .querySelector("[data-syllabus-change]")
    .addEventListener("click", function () {
      clearPick();
      file.click();
    });

  form.elements.board.addEventListener("change", function () {
    form.querySelector("[data-board-name]").hidden =
      form.elements.board.value !== "state";
    field.hidden = form.elements.board.value === "none";
    clearPick();
  });

  form.querySelectorAll("[data-access]").forEach(function (button) {
    button.addEventListener("click", function () {
      var web = button.dataset.access === "web";
      form.querySelectorAll("[data-access]").forEach(function (other) {
        other.setAttribute("aria-pressed", other === button ? "true" : "false");
      });
      form.elements.web_access.value = button.dataset.access;
      form.querySelector("[data-sites]").hidden = !web;
    });
  });

  [
    "term_start",
    "term_end",
    "class_minutes",
    "time_0",
    "time_1",
    "time_2",
    "time_3",
    "time_4",
    "time_5",
  ].forEach(function (name) {
    form.elements[name].addEventListener("input", updateChip);
  });
})();
