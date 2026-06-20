/* Populate the navbar Discord button with the live online member count
   from the public guild widget. Degrades silently if the request fails
   (e.g. widget disabled or offline) — the button still links to the invite. */
(function () {
  var GUILD = "1516456431786786817";
  fetch("https://discord.com/api/guilds/" + GUILD + "/widget.json")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || typeof d.presence_count !== "number") return;
      var label = d.presence_count + " online";
      document.querySelectorAll("[data-discord-count]").forEach(function (el) {
        el.textContent = label;
        el.hidden = false;
      });
    })
    .catch(function () {});
})();
