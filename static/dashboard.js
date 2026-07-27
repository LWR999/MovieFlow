// Foreign checkbox toggle
document.querySelectorAll(".foreign-checkbox").forEach(function (cb) {
    cb.addEventListener("change", function () {
        fetch(`/movies/${cb.dataset.movieId}/foreign`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `is_foreign=${cb.checked ? "1" : "0"}`,
        });
    });
});

// Filter chips
document.querySelectorAll(".chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
        document.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        const filter = chip.dataset.filter;
        document.querySelectorAll("tr[data-filter-group]").forEach(function (row) {
            row.style.display = filter === "all" || row.dataset.filterGroup === filter ? "" : "none";
        });
    });
});

// Bulk selection counts + submit
function updateBulkCounts() {
    ["stage", "preprocess", "process", "library"].forEach(function (group) {
        const n = document.querySelectorAll(`.row-select[data-bulk-group="${group}"]:checked`).length;
        const countEl = document.querySelector(`[data-bulk-count="${group}"]`);
        if (countEl) countEl.textContent = n;
        const btn = document.querySelector(`[data-bulk-submit="${group}"]`);
        if (btn) btn.disabled = n === 0;
    });
}

document.querySelectorAll(".row-select").forEach(function (cb) {
    cb.addEventListener("change", updateBulkCounts);
});

document.getElementById("select-all")?.addEventListener("click", function () {
    document.querySelectorAll(".row-select").forEach((cb) => (cb.checked = true));
    updateBulkCounts();
});
document.getElementById("select-none")?.addEventListener("click", function () {
    document.querySelectorAll(".row-select").forEach((cb) => (cb.checked = false));
    updateBulkCounts();
});

document.querySelectorAll("[data-bulk-submit]").forEach(function (btn) {
    btn.addEventListener("click", function () {
        const group = btn.dataset.bulkSubmit;
        const form = document.getElementById(`bulk-form-${group}`);
        form.querySelectorAll('input[name="movie_id"]').forEach((el) => el.remove());
        document.querySelectorAll(`.row-select[data-bulk-group="${group}"]:checked`).forEach(function (cb) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "movie_id";
            input.value = cb.dataset.movieId;
            form.appendChild(input);
        });
        form.submit();
    });
});

updateBulkCounts();
