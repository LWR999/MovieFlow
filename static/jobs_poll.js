function pollJobs() {
    document.querySelectorAll("tr[data-job-id]").forEach(function (row) {
        const jobId = row.dataset.jobId;
        fetch(`/api/jobs/${jobId}`)
            .then((r) => r.json())
            .then((job) => {
                const fill = row.querySelector(".progress-bar-fill");
                if (fill) fill.style.width = `${job.progress_pct}%`;
                const msg = row.querySelector(".job-message");
                if (msg) msg.textContent = job.message || "";
                if (job.status === "done" || job.status === "error") {
                    location.reload();
                }
            });
    });
}

setInterval(pollJobs, 1000);
