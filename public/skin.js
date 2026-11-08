(function () {
  var skins = ["aura", "neon", "paper", "arcade", "terminal"], s = new URLSearchParams(location.search).get("skin");
  try { s = s || localStorage.getItem("shopwiz.skin"); } catch (e) {}
  document.documentElement.dataset.skin = skins.indexOf(s) >= 0 ? s : "aura";
})();
