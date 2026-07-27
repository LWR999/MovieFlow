document.getElementById("select-all")?.addEventListener("click", () => {
    document.querySelectorAll(".row-select").forEach((cb) => (cb.checked = true));
});
document.getElementById("select-none")?.addEventListener("click", () => {
    document.querySelectorAll(".row-select").forEach((cb) => (cb.checked = false));
});
