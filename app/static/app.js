document.addEventListener("submit", function (e) {
  var form = e.target;
  if (!form.method || form.method.toLowerCase() !== "post") return;
  if (form.dataset.submitting) { e.preventDefault(); return; }
  var btn = form.querySelector('button[type="submit"]');
  if (!btn) return;
  form.dataset.submitting = "1";
  btn.dataset.label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "جارٍ المعالجة...";
});

// لو رجع المستخدم بزر Back
window.addEventListener("pageshow", function (e) {
  if (!e.persisted) return;
  document.querySelectorAll("form[data-submitting]").forEach(function (form) {
    delete form.dataset.submitting;
    var btn = form.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.label || btn.textContent; }
  });
});