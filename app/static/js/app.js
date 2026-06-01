document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-copy-tags]").forEach((button) => {
        button.addEventListener("click", async () => {
            const tags = button.dataset.tags || "";
            if (!tags) {
                return;
            }
            try {
                await navigator.clipboard.writeText(tags);
                const original = button.innerHTML;
                button.innerHTML = '<i class="bi bi-check2"></i> Copied';
                window.setTimeout(() => {
                    button.innerHTML = original;
                }, 1400);
            } catch {
                const textarea = document.createElement("textarea");
                textarea.value = tags;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                textarea.remove();
            }
        });
    });

    document.querySelectorAll("[data-creative-upload]").forEach((input) => {
        input.addEventListener("change", () => {
            const file = input.files && input.files[0];
            const shell = input.closest(".creative-upload");
            if (!file || !shell) {
                return;
            }
            const preview = shell.querySelector("[data-creative-preview]");
            const empty = shell.querySelector("[data-creative-empty]");
            preview.src = URL.createObjectURL(file);
            preview.hidden = false;
            empty.hidden = true;
        });
    });

    async function generateImage(button) {
        const original = button.innerHTML;
        const card = button.closest("[data-concept-card]");
        const slot = card && card.querySelector("[data-image-slot]");
        const providerSelect = document.querySelector("[data-image-provider]");
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Generating';
        try {
            const response = await fetch(button.dataset.endpoint, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    prompt: button.dataset.prompt,
                    aspect_ratio: button.dataset.aspectRatio || "1:1",
                    provider: providerSelect ? providerSelect.value : "openai",
                }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || "Image generation failed");
            }
            if (slot) {
                slot.hidden = false;
                slot.innerHTML = `<img src="${payload.image_url}" alt="Generated ad concept">`;
            }
            button.innerHTML = '<i class="bi bi-check2-circle"></i> Image Generated';
            button.classList.add("generated");
        } catch (error) {
            if (slot) {
                slot.hidden = false;
                slot.innerHTML = `<div class="creative-error">${error.message}</div>`;
            }
            button.innerHTML = original;
        } finally {
            button.disabled = false;
        }
    }

    document.querySelectorAll("[data-generate-image]").forEach((button) => {
        button.addEventListener("click", () => generateImage(button));
    });

    const bulkButton = document.querySelector("[data-mark-all-generated]");
    if (bulkButton) {
        bulkButton.addEventListener("click", async () => {
            const buttons = Array.from(document.querySelectorAll("[data-generate-image]:not(.generated)"));
            if (!buttons.length || !window.confirm(`Generate ${buttons.length} images now?`)) {
                return;
            }
            bulkButton.disabled = true;
            for (const button of buttons) {
                await generateImage(button);
            }
            bulkButton.disabled = false;
            bulkButton.innerHTML = '<i class="bi bi-check2-circle"></i> All Images Requested';
        });
    }

    const modalEl = document.getElementById("noteDetailModal");
    if (!modalEl) {
        return;
    }

    const modal = new bootstrap.Modal(modalEl);
    const field = (name) => modalEl.querySelector(`[data-note-field="${name}"]`);

    async function openDetail(url) {
        const response = await fetch(url);
        if (!response.ok) {
            return;
        }
        const note = await response.json();
        field("title").textContent = note.title || "内容详情";
        field("meta").textContent = `${note.author || "未知作者"} · 采集于 ${note.collection_time || "-"}`;
        field("product_keyword").textContent = note.product_keyword || "-";
        field("likes_count").textContent = note.likes_count ?? 0;
        field("comments_count").textContent = note.comments_count ?? 0;
        field("publish_time").textContent = note.publish_time || "-";
        field("content").textContent = note.content || "暂无正文";

        const commentsEl = field("triggered_comments");
        commentsEl.innerHTML = "";
        const comments = Array.isArray(note.triggered_comments) ? note.triggered_comments : [];
        if (comments.length === 0) {
            commentsEl.innerHTML = '<div class="triggered-comment text-muted">暂无触发评论</div>';
        } else {
            comments.forEach((comment) => {
                const item = document.createElement("div");
                item.className = "triggered-comment";
                item.textContent = typeof comment === "string" ? comment : JSON.stringify(comment);
                commentsEl.appendChild(item);
            });
        }

        const source = field("source_url");
        if (note.source_url) {
            source.href = note.source_url;
            source.classList.remove("disabled");
        } else {
            source.removeAttribute("href");
            source.classList.add("disabled");
        }

        modal.show();
    }

    document.querySelectorAll(".note-row").forEach((row) => {
        row.addEventListener("click", (event) => {
            if (event.target.closest("a, button")) {
                return;
            }
            openDetail(row.dataset.detailUrl);
        });
    });

    document.querySelectorAll(".note-action").forEach((button) => {
        button.addEventListener("click", () => openDetail(button.dataset.detailUrl));
    });
});
