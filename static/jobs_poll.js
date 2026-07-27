function pollJobs() {
    document.querySelectorAll("tr[data-job-id]").forEach(function (row) {
        const jobId = row.dataset.jobId;
        fetch(`/api/jobs/${jobId}`)
            .then((r) => r.json())
            .then((job) => {
                row.querySelector(".job-status").textContent = job.status;
                row.querySelector(".job-message").textContent = job.message || "";
                const fill = row.querySelector(".progress-bar-fill");
                if (fill) fill.style.width = `${job.progress_pct}%`;
                if (job.status === "done") {
                    row.remove();
                }
            });
    });
}

setInterval(pollJobs, 1000);
