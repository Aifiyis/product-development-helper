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

    const competitorModalEl = document.getElementById("competitorProductModal");
    if (competitorModalEl) {
        const competitorModal = new bootstrap.Modal(competitorModalEl);
        const cfield = (name) => competitorModalEl.querySelector(`[data-competitor-field="${name}"]`);
        const escapeHtml = (value) => String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");

        async function openCompetitorDetail(url) {
            const response = await fetch(url);
            if (!response.ok) {
                return;
            }
            const product = await response.json();
            cfield("title").textContent = product.title || "产品详情";
            cfield("meta").textContent = `${product.source_type || "-"} · ${product.collected_at || "-"}`;
            cfield("source_domain").textContent = product.source_domain || "-";
            cfield("price").textContent = product.price || "-";
            cfield("product_created_at").textContent = product.product_created_at || "-";
            cfield("reviews_count").textContent = product.reviews_count ?? 0;
            cfield("fb_ad_count").textContent = product.fb_ad_count ?? "-";
            cfield("description").innerHTML = product.description || "暂无描述";
            const tags = Array.isArray(product.product_tags) ? product.product_tags : [];
            cfield("product_tags").innerHTML = tags.length
                ? tags.map((tag) => `<span class="detail-tag">${escapeHtml(tag)}</span>`).join("")
                : "-";

            const mediaEl = cfield("media");
            const mediaThumbsEl = cfield("media_thumbs");
            const media = product.product_media || {};
            const images = Array.from(new Set([media.main, ...(media.carousel || [])].filter(Boolean)));
            mediaEl.innerHTML = images.length
                ? images.map((src, index) => `<div class="carousel-item ${index === 0 ? "active" : ""}"><img src="${src}" alt=""></div>`).join("")
                : '<div class="carousel-item active"><div class="competitor-media-empty">暂无图片</div></div>';
            mediaThumbsEl.innerHTML = images.length
                ? images.map((src, index) => `<button class="media-thumb ${index === 0 ? "active" : ""}" type="button" data-media-index="${index}"><img src="${src}" alt=""></button>`).join("")
                : "";
            const carousel = bootstrap.Carousel.getOrCreateInstance(document.getElementById("competitorMediaCarousel"), {interval: false});
            mediaThumbsEl.querySelectorAll("[data-media-index]").forEach((thumb) => {
                thumb.addEventListener("click", () => {
                    const index = Number(thumb.dataset.mediaIndex || 0);
                    carousel.to(index);
                    mediaThumbsEl.querySelectorAll(".media-thumb").forEach((item) => item.classList.remove("active"));
                    thumb.classList.add("active");
                });
            });
            document.getElementById("competitorMediaCarousel").addEventListener("slid.bs.carousel", (event) => {
                mediaThumbsEl.querySelectorAll(".media-thumb").forEach((item) => item.classList.remove("active"));
                const activeThumb = mediaThumbsEl.querySelector(`[data-media-index="${event.to}"]`);
                if (activeThumb) {
                    activeThumb.classList.add("active");
                }
            }, {once: true});

            const variantsEl = cfield("variants");
            const variants = Array.isArray(product.variants) ? product.variants : [];
            variantsEl.innerHTML = variants.length
                ? variants.map((variant) => {
                    const values = Array.isArray(variant.values) && variant.values.length ? ` · ${variant.values.join(" / ")}` : "";
                    const source = variant.source ? ` · ${variant.source}` : "";
                    return `<div class="triggered-comment">${variant.title || "-"} · ${variant.price || "-"} · ${variant.available === false ? "售罄" : "可售"}${source}${values}</div>`;
                }).join("")
                : '<div class="triggered-comment text-muted">暂无变体</div>';

            const link = cfield("product_url");
            if (product.product_url) {
                link.href = product.product_url;
                link.classList.remove("disabled");
            } else {
                link.removeAttribute("href");
                link.classList.add("disabled");
            }
            competitorModal.show();
        }

        document.querySelectorAll(".competitor-product-row").forEach((row) => {
            row.addEventListener("click", (event) => {
                if (event.target.closest("a, button")) {
                    return;
                }
                openCompetitorDetail(row.dataset.detailUrl);
            });
        });
        document.querySelectorAll(".competitor-product-action").forEach((button) => {
            button.addEventListener("click", () => openCompetitorDetail(button.dataset.detailUrl));
        });
    }

    document.querySelectorAll("[data-site-search]").forEach((input) => {
        const select = input.parentElement && input.parentElement.querySelector("[data-site-select]");
        const categoryFilter = document.querySelector("[data-site-category-filter]");
        if (!select) {
            return;
        }
        const options = Array.from(select.options).map((option) => ({
            value: option.value,
            text: option.text,
            category: option.dataset.category || "",
            selected: option.selected,
        }));
        function renderSiteOptions() {
            const keyword = input.value.trim().toLowerCase();
            const category = categoryFilter ? categoryFilter.value : "";
            const selectedValues = new Set(Array.from(select.selectedOptions).map((option) => option.value));
            select.innerHTML = "";
            options
                .filter((option) => {
                    const categoryMatched = !category || option.category === category || selectedValues.has(option.value);
                    const keywordMatched = !keyword || option.text.toLowerCase().includes(keyword) || selectedValues.has(option.value);
                    return categoryMatched && keywordMatched;
                })
                .forEach((item) => {
                    const option = new Option(item.text, item.value);
                    option.dataset.category = item.category;
                    option.selected = selectedValues.has(item.value);
                    select.add(option);
                });
        }
        input.addEventListener("input", renderSiteOptions);
        if (categoryFilter) {
            categoryFilter.addEventListener("change", renderSiteOptions);
        }
    });

    function showCompetitorTaskNotice(message) {
        const noticeEl = document.getElementById("competitorTaskNoticeModal");
        const noticeBody = document.querySelector("[data-competitor-task-notice]");
        if (!noticeEl || !noticeBody) {
            window.alert(message);
            return;
        }
        noticeBody.textContent = message;
        bootstrap.Modal.getOrCreateInstance(noticeEl).show();
    }

    const escapeTaskHtml = (value) => String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");

    function upsertCompetitorTaskRow(task) {
        const tbody = document.querySelector("[data-competitor-task-body]");
        if (!tbody || !task) {
            return;
        }
        const emptyRow = tbody.querySelector("[data-task-empty]");
        if (emptyRow) {
            emptyRow.remove();
        }
        const existing = tbody.querySelector(`[data-task-row="${task.id}"]`);
        const row = existing || document.createElement("tr");
        row.dataset.taskRow = task.id;
        row.innerHTML = `
            <td>${escapeTaskHtml(task.category_label || "不限")}</td>
            <td class="task-sites">${escapeTaskHtml((task.sites || []).join(", "))}</td>
            <td>${escapeTaskHtml(task.product_keywords || "-")}</td>
            <td>${escapeTaskHtml(task.condition || "-")}</td>
            <td>${escapeTaskHtml(task.cycle_label || "-")}</td>
            <td><span class="badge text-bg-primary" data-task-status="${task.id}">采集中</span></td>
            <td class="text-end"><span class="muted-text">刷新后可操作</span></td>
        `;
        if (!existing) {
            tbody.prepend(row);
        }
    }

    function updateCompetitorTaskStatus(payload) {
        const status = document.querySelector(`[data-task-status="${payload.task_id}"]`);
        if (!status) {
            return;
        }
        status.className = `badge text-bg-${payload.status_badge || "primary"}`;
        status.textContent = payload.status_label || "采集中";
    }

    function pollCompetitorTask(taskId) {
        const poll = async () => {
            try {
                const response = await fetch(`/competitor/tasks/${taskId}/status`);
                if (!response.ok) {
                    return;
                }
                const payload = await response.json();
                updateCompetitorTaskStatus(payload);
                if (payload.status === "completed" || (payload.status === "collecting" && payload.last_run_at)) {
                    showCompetitorTaskNotice("采集已完成，请手动刷新页面更新数据。");
                    return;
                }
                if (payload.status === "failed") {
                    showCompetitorTaskNotice(`采集失败，请手动刷新查看错误信息。${payload.last_error ? `\n${payload.last_error}` : ""}`);
                    return;
                }
                window.setTimeout(poll, 5000);
            } catch {
                window.setTimeout(poll, 8000);
            }
        };
        window.setTimeout(poll, 5000);
    }

    document.querySelectorAll("[data-competitor-task-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const saveButton = form.querySelector("[data-competitor-save]");
            const overlay = document.querySelector("[data-competitor-loading]");
            const loadingText = document.querySelector("[data-competitor-loading-text]");
            const modalEl = form.closest(".modal");
            const modal = modalEl ? bootstrap.Modal.getOrCreateInstance(modalEl) : null;
            const originalButton = saveButton ? saveButton.innerHTML : "";

            if (saveButton) {
                saveButton.disabled = true;
                saveButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> 后台采集中';
            }
            if (loadingText) {
                loadingText.textContent = "后台采集中";
            }
            if (overlay) {
                overlay.hidden = false;
                window.setTimeout(() => {
                    overlay.hidden = true;
                }, 3000);
            }
            if (modal) {
                modal.hide();
            }

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {"X-Requested-With": "XMLHttpRequest"},
                    body: new FormData(form),
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.message || "创建任务失败");
                }
                if (payload.task) {
                    window.setTimeout(() => upsertCompetitorTaskRow(payload.task), 3000);
                    pollCompetitorTask(payload.task.id);
                }
                form.reset();
            } catch (error) {
                if (overlay) {
                    overlay.hidden = true;
                }
                showCompetitorTaskNotice(error.message || "创建任务失败");
            } finally {
                if (saveButton) {
                    saveButton.disabled = false;
                    saveButton.innerHTML = originalButton;
                }
            }
        });
    });

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
