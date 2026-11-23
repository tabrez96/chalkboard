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

  form.elements.board.addEventListener("change", function () {
    form.querySelector("[data-board-name]").hidden =
      form.elements.board.value !== "state";
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
