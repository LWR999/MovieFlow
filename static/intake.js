document.querySelectorAll(".foreign-checkbox").forEach(function (cb) {
    cb.addEventListener("change", function () {
        fetch(`/movies/${cb.dataset.movieId}/foreign`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `is_foreign=${cb.checked ? "1" : "0"}`,
        });
    });
});
