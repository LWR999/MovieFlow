document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
        if (!confirm(form.dataset.confirm)) {
            e.preventDefault();
        }
    });
});
