(function () {
  var count = 0;
  var countEl = document.getElementById("count");
  var btn = document.getElementById("btn");

  btn.addEventListener("click", function () {
    count += 1;
    countEl.textContent = String(count);
  });
})();
