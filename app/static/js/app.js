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
            cfield("platform").textContent = product.platform_label || product.platform || "未知";
            const oldPrice = product.previous_price
                ? `<span class="text-muted ms-2">${escapeHtml(product.previous_price)}（${escapeHtml(product.previous_collected_at || "-")}）</span>`
                : "";
            cfield("price").innerHTML = `<span>${escapeHtml(product.price || "-")}</span>${oldPrice}`;
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
            cfield("media_count").textContent = images.length;
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
            const groupedVariants = new Map();
            variants.forEach((variant) => {
                const optionValues = variant.option_values || variant.options;
                if (optionValues && typeof optionValues === "object" && !Array.isArray(optionValues)) {
                    Object.entries(optionValues).forEach(([name, value]) => {
                        if (!name || !value) return;
                        if (!groupedVariants.has(name)) groupedVariants.set(name, []);
                        const values = groupedVariants.get(name);
                        if (!values.includes(String(value))) values.push(String(value));
                    });
                }
            });
            if (groupedVariants.size) {
                variantsEl.innerHTML = Array.from(groupedVariants.entries()).map(([name, values]) =>
                    `<div class="triggered-comment"><strong>${escapeHtml(name)}</strong> · ${escapeHtml(values.join(" / "))}</div>`
                ).join("");
            } else {
                variantsEl.innerHTML = variants.length
                    ? variants.map((variant) => {
                        const values = Array.isArray(variant.values) && variant.values.length ? ` · ${escapeHtml(variant.values.join(" / "))}` : "";
                        return `<div class="triggered-comment">${escapeHtml(variant.title || "-")}${values} · ${variant.available === false ? "售罄" : "可售"}</div>`;
                    }).join("")
                    : '<div class="triggered-comment text-muted">暂无变体</div>';
            }

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
                if (event.target.closest("a, button, input, label, form")) {
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

    document.querySelectorAll("[data-competitor-task-form]").forEach((form) => {
        const siteFields = form.querySelector("[data-competitor-sites-fields]");
        const linkFields = form.querySelector("[data-competitor-links-fields]");
        const categoryFields = form.querySelector("[data-competitor-category-fields]");
        const siteOptions = form.querySelector("[data-competitor-site-options]");
        const siteSelect = form.querySelector("[data-site-select]");
        const categorySelect = form.querySelector("[data-site-category-filter]");
        const siteSearch = form.querySelector("[data-site-search]");
        const linkTextarea = form.querySelector("[name='product_urls']");
        const categoryUrl = form.querySelector("[data-category-url]");
        const categoryScope = form.querySelector("[data-category-scope]");
        const categoryPageCount = form.querySelector("[data-category-page-count]");
        const categoryPageCountWrap = form.querySelector("[data-category-page-count-wrap]");
        const cycle = form.querySelector("[data-competitor-cycle]");

        function syncCategoryScope() {
            const usePageCount = categoryScope?.value === "pages";
            if (categoryPageCountWrap) categoryPageCountWrap.hidden = !usePageCount;
            if (categoryPageCount) {
                categoryPageCount.disabled = !usePageCount || categoryScope.disabled;
                categoryPageCount.required = usePageCount && !categoryScope.disabled;
            }
        }

        function syncCollectionMode() {
            const mode = form.querySelector("[data-competitor-collection-mode]:checked")?.value || "competitor_sites";
            const isSiteCollection = mode === "competitor_sites";
            const isLinkCollection = mode === "product_links";
            const isCategoryCollection = mode === "category";
            if (siteFields) siteFields.hidden = !isSiteCollection;
            if (siteOptions) siteOptions.hidden = !isSiteCollection;
            if (linkFields) linkFields.hidden = !isLinkCollection;
            if (categoryFields) categoryFields.hidden = !isCategoryCollection;
            if (siteSelect) {
                siteSelect.disabled = !isSiteCollection;
                siteSelect.required = isSiteCollection;
            }
            if (categorySelect) categorySelect.disabled = !isSiteCollection;
            if (siteSearch) siteSearch.disabled = !isSiteCollection;
            if (linkTextarea) {
                linkTextarea.disabled = !isLinkCollection;
                linkTextarea.required = isLinkCollection;
            }
            if (categoryUrl) {
                categoryUrl.disabled = !isCategoryCollection;
                categoryUrl.required = isCategoryCollection;
            }
            if (categoryScope) categoryScope.disabled = !isCategoryCollection;
            if (siteOptions) {
                siteOptions.querySelectorAll("input, select").forEach((field) => {
                    field.disabled = !isSiteCollection;
                });
            }
            if (cycle) {
                cycle.querySelectorAll("option").forEach((option) => {
                    const hideOption = isLinkCollection && option.value !== "instant";
                    option.hidden = hideOption;
                    option.disabled = hideOption;
                });
                if (isLinkCollection) cycle.value = "instant";
            }
            syncCategoryScope();
        }

        function isCollectionUrl(value) {
            try {
                const url = new URL(value);
                const path = url.pathname.toLowerCase().replace(/\/$/, "");
                return path === "/collections" || path.startsWith("/collections/");
            } catch (error) {
                return false;
            }
        }

        linkTextarea?.addEventListener("input", () => {
            const urls = linkTextarea.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
            if (urls.length !== 1 || !isCollectionUrl(urls[0])) return;
            if (categoryUrl) categoryUrl.value = urls[0];
            const categoryMode = form.querySelector('[data-competitor-collection-mode][value="category"]');
            if (categoryMode) categoryMode.checked = true;
            syncCollectionMode();
        });
        categoryScope?.addEventListener("change", syncCategoryScope);
        form.querySelectorAll("[data-competitor-collection-mode]").forEach((radio) => {
            radio.addEventListener("change", syncCollectionMode);
        });
        form.addEventListener("reset", () => window.setTimeout(syncCollectionMode, 0));
        syncCollectionMode();
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

    function taskSitesDisclosure(sites) {
        const values = Array.isArray(sites) ? sites.filter(Boolean) : [];
        if (!values.length) return "-";
        const summary = values.length > 1 ? `${values[0]} 等 ${values.length} 项` : values[0];
        return `<details class="task-sites-disclosure"><summary>${escapeTaskHtml(summary)}</summary><div>${values.map((value) => `<div>${escapeTaskHtml(value)}</div>`).join("")}</div></details>`;
    }
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
            <td>${taskSitesDisclosure(task.sites || [])}</td>
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

document.addEventListener("DOMContentLoaded", () => {
    const selectAllProducts = document.querySelector("[data-select-products]");
    const productCheckboxes = Array.from(document.querySelectorAll("[data-product-select]:not(:disabled)"));
    const moveSelectedButton = document.querySelector("[data-move-selected]");
    const updateBulkMoveState = () => {
        const selectedCount = productCheckboxes.filter((item) => item.checked).length;
        if (moveSelectedButton) moveSelectedButton.disabled = selectedCount === 0;
        if (selectAllProducts) {
            selectAllProducts.checked = productCheckboxes.length > 0 && selectedCount === productCheckboxes.length;
            selectAllProducts.indeterminate = selectedCount > 0 && selectedCount < productCheckboxes.length;
        }
    };
    selectAllProducts?.addEventListener("change", () => {
        productCheckboxes.forEach((item) => { item.checked = selectAllProducts.checked; });
        updateBulkMoveState();
    });
    productCheckboxes.forEach((item) => item.addEventListener("change", updateBulkMoveState));
    updateBulkMoveState();

    document.querySelectorAll("[data-store-search]").forEach((input) => {
        input.addEventListener("input", () => {
            const keyword = input.value.trim().toLowerCase();
            input.closest(".modal")?.querySelectorAll("[data-store-name]").forEach((item) => {
                item.hidden = keyword !== "" && !item.dataset.storeName.includes(keyword);
            });
        });
    });

    document.querySelectorAll("[data-store-form]").forEach((form) => {
        const platformField = form.querySelector("[data-store-platform]");
        const shopify = form.querySelector("[data-shopify-credentials]");
        const shoplazza = form.querySelector("[data-shoplazza-credentials]");
        const domain = form.querySelector("[data-store-domain]");
        const updatePlatformFields = () => {
            const platform = form.dataset.fixedPlatform || platformField?.value || "shopify";
            if (shopify) shopify.hidden = platform !== "shopify";
            if (shoplazza) shoplazza.hidden = platform !== "shoplazza";
            if (domain) domain.placeholder = platform === "shopify" ? "example.myshopify.com" : "example.myshoplaza.com";
        };
        platformField?.addEventListener("change", updatePlatformFields);
        updatePlatformFields();
    });

    const processingRows = Array.from(document.querySelectorAll("[data-draft-row][data-status-url]"))
        .filter((row) => ["drafting", "publishing"].includes(row.dataset.syncStatus));
    if (processingRows.length) {
        const poll = async () => {
            let completed = false;
            await Promise.all(processingRows.map(async (row) => {
                if (!row.isConnected || !["drafting", "publishing"].includes(row.dataset.syncStatus)) return;
                try {
                    const response = await fetch(row.dataset.statusUrl, { headers: { Accept: "application/json" } });
                    if (!response.ok) return;
                    const payload = await response.json();
                    row.dataset.syncStatus = payload.status;
                    const badge = row.querySelector("[data-draft-status]");
                    if (badge) {
                        badge.className = `badge text-bg-${payload.status_badge}`;
                        badge.textContent = payload.status_label;
                    }
                    if (!["drafting", "publishing"].includes(payload.status)) completed = true;
                } catch (error) {
                    // Polling failures do not alter the background publish task.
                }
            }));
            if (completed) window.location.reload();
        };
        window.setInterval(poll, 3000);
        poll();
    }

    const editorForm = document.querySelector("[data-product-editor]");
    if (!editorForm) return;
    const parseData = (id, fallback) => {
        try { return JSON.parse(document.getElementById(id)?.textContent || ""); }
        catch (error) { return fallback; }
    };
    let options = parseData("editorOptionsData", []);
    let variants = parseData("editorVariantsData", []);
    const optionEditor = editorForm.querySelector("[data-option-editor]");
    const optionsJson = editorForm.querySelector("[data-options-json]");
    const variantHead = editorForm.querySelector("[data-variant-head]");
    const variantBody = editorForm.querySelector("[data-variant-body]");
    const variantCount = editorForm.querySelector("[data-variant-count]");
    const variantCountLabel = editorForm.querySelector("[data-variant-count-label]");
    const addOption = editorForm.querySelector("[data-add-option]");
    const baseSkuInput = editorForm.querySelector("[data-base-sku]");
    const skuOptionSelector = editorForm.querySelector("[data-sku-option-selector]");
    const generateSkuButton = editorForm.querySelector("[data-generate-sku]");
    const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    const shopifyReferenceEndpoint = editorForm.dataset.shopifyReferenceEndpoint;
    const shopifyCategoryEndpoint = editorForm.dataset.shopifyCategoryEndpoint;
    const productTitleInput = editorForm.querySelector('[name="title"]');
    const classicSearchSelects = Array.from(editorForm.querySelectorAll("[data-classic-search-select]"));
    const renderClassicOptions = (container) => {
        const input = container.querySelector("[data-classic-search-input]");
        const optionsPanel = container.querySelector("[data-classic-search-options]");
        const query = input.value.trim().toLowerCase();
        const values = (container.classicValues || []).filter((value) => (
            !query || value.toLowerCase().includes(query)
        ));
        optionsPanel.innerHTML = values.length
            ? values.map((value) => `<button type="button" data-classic-option="${escapeHtml(value)}">${escapeHtml(value)}</button>`).join("")
            : '<div class="classic-search-empty">暂无匹配选项</div>';
        optionsPanel.hidden = false;
    };
    const setClassicOptions = (source, values) => {
        const container = classicSearchSelects.find((item) => item.dataset.classicSource === source);
        if (!container) return;
        container.classicValues = Array.from(new Set((values || []).map(String).filter(Boolean)));
        if (document.activeElement === container.querySelector("[data-classic-search-input]")) {
            renderClassicOptions(container);
        }
    };
    classicSearchSelects.forEach((container) => {
        container.classicValues = [];
        const input = container.querySelector("[data-classic-search-input]");
        const optionsPanel = container.querySelector("[data-classic-search-options]");
        input.addEventListener("focus", () => renderClassicOptions(container));
        input.addEventListener("click", () => renderClassicOptions(container));
        input.addEventListener("input", () => renderClassicOptions(container));
        input.addEventListener("blur", () => {
            window.setTimeout(() => { optionsPanel.hidden = true; }, 120);
        });
        optionsPanel.addEventListener("mousedown", (event) => {
            const option = event.target.closest("[data-classic-option]");
            if (!option) return;
            event.preventDefault();
            input.value = option.dataset.classicOption;
            input.dispatchEvent(new Event("change", { bubbles: true }));
            optionsPanel.hidden = true;
        });
    });
    if (shopifyReferenceEndpoint) {
        fetch(shopifyReferenceEndpoint)
            .then(async (response) => {
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || "Shopify 编辑数据加载失败");
                return payload;
            })
            .then((payload) => {
                setClassicOptions("product-types", payload.product_types || []);
                Object.entries(payload.metafield_choices || {}).forEach(([key, choices]) => {
                    setClassicOptions(`metafield:${key}`, choices || []);
                });
            })
            .catch((error) => console.warn(error.message));
    }
    const categoryPicker = editorForm.querySelector("[data-category-picker]");
    if (categoryPicker && shopifyCategoryEndpoint) {
        const categoryId = categoryPicker.querySelector("[data-category-id]");
        const categorySearch = categoryPicker.querySelector("[data-category-search]");
        const categorySuggestions = categoryPicker.querySelector("[data-category-suggestions]");
        const categoryStatus = categoryPicker.querySelector("[data-category-status]");
        const categoryClear = categoryPicker.querySelector("[data-category-clear]");
        let selectedCategoryName = categorySearch.value;
        let categoryTimer = null;
        let hideCategorySuggestions = false;
        const renderCategories = (categories, suggested) => {
            categorySuggestions.innerHTML = categories.map((category) => `
                <button type="button" data-category-option data-category-id="${escapeHtml(category.id)}" data-category-name="${escapeHtml(category.full_name || category.name)}">
                    <span>${escapeHtml(category.full_name || category.name)}</span>
                    ${suggested ? "<small>推荐</small>" : ""}
                </button>
            `).join("");
            categorySuggestions.hidden = !categories.length || hideCategorySuggestions;
            categoryStatus.textContent = categories.length
                ? `${categories.length} 个${suggested ? "推荐" : "搜索"}结果`
                : "没有找到匹配的 Shopify Category";
        };
        const loadCategories = async ({ query = "", suggested = false } = {}) => {
            const url = new URL(shopifyCategoryEndpoint, window.location.origin);
            if (suggested) {
                url.searchParams.set("recommend", "1");
                url.searchParams.set("title", productTitleInput?.value || "");
                categoryStatus.textContent = "正在按产品标题推荐…";
            } else {
                url.searchParams.set("q", query);
                categoryStatus.textContent = "正在搜索 Category…";
            }
            try {
                const response = await fetch(url);
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || "Category 加载失败");
                renderCategories(payload.categories || [], suggested);
            } catch (error) {
                categorySuggestions.hidden = true;
                categoryStatus.textContent = error.message;
            }
        };
        categorySuggestions.addEventListener("click", (event) => {
            const option = event.target.closest("[data-category-option]");
            if (!option) return;
            categoryId.value = option.dataset.categoryId;
            categorySearch.value = option.dataset.categoryName;
            selectedCategoryName = option.dataset.categoryName;
            categorySuggestions.hidden = true;
            categoryStatus.textContent = "已选择 Shopify Category";
        });
        categorySearch.addEventListener("blur", () => {
            hideCategorySuggestions = true;
            window.setTimeout(() => { categorySuggestions.hidden = true; }, 150);
        });
        categorySearch.addEventListener("focus", () => {
            hideCategorySuggestions = false;
            if (!categoryId.value && !categorySearch.value.trim()) {
                loadCategories({ suggested: true });
            }
        });
        categorySearch.addEventListener("input", () => {
            if (categorySearch.value !== selectedCategoryName) categoryId.value = "";
            window.clearTimeout(categoryTimer);
            const query = categorySearch.value.trim();
            if (query.length < 2) {
                categorySuggestions.hidden = true;
                categoryStatus.textContent = "输入至少 2 个字符搜索 Category";
                return;
            }
            categoryTimer = window.setTimeout(() => loadCategories({ query }), 300);
        });
        categoryClear.addEventListener("click", () => {
            hideCategorySuggestions = true;
            categoryId.value = "";
            categorySearch.value = "";
            selectedCategoryName = "";
            categorySuggestions.hidden = true;
            categoryStatus.textContent = "已清除 Category";
        });
        productTitleInput?.addEventListener("change", () => {
            if (!categoryId.value) loadCategories({ suggested: true });
        });
        if (!categoryId.value) loadCategories({ suggested: true });
    }
    const signature = (values) => JSON.stringify(options.map((option) => [option.name, values?.[option.name] || ""]));
    const readOptions = () => Array.from(optionEditor.querySelectorAll("[data-option-row]")).map((row) => ({
        name: row.querySelector("[data-option-name]").value.trim(),
        values: Array.from(new Set(row.querySelector("[data-option-values]").value.split(",").map((value) => value.trim()).filter(Boolean))),
    })).filter((option) => option.name && option.values.length);
    const readVariantTable = () => {
        const state = new Map();
        variantBody.querySelectorAll("[data-variant-row]").forEach((row) => {
            const index = row.dataset.variantRow;
            const values = JSON.parse(row.querySelector(`[name="variant_options-${index}"]`).value);
            state.set(signature(values), {
                id: Number(row.querySelector(`[name="variant_id-${index}"]`).value || 0), options: values,
                image_url: row.dataset.imageUrl || "", sku: row.querySelector(`[name="variant_sku-${index}"]`).value,
                price: row.querySelector(`[name="variant_price-${index}"]`).value,
                compare_at_price: row.querySelector(`[name="variant_compare_at-${index}"]`).value,
                inventory_quantity: row.querySelector(`[name="variant_inventory-${index}"]`).value,
                weight_kg: row.querySelector(`[name="variant_weight-${index}"]`).value,
                package_length_cm: row.querySelector(`[name="variant_length-${index}"]`).value,
                package_width_cm: row.querySelector(`[name="variant_width-${index}"]`).value,
                package_height_cm: row.querySelector(`[name="variant_height-${index}"]`).value,
            });
        });
        return state;
    };
    const deepClone = (value) => JSON.parse(JSON.stringify(value));
    const normalizeSkuPart = (value) => String(value || "").trim().replace(/\s+/g, "-").replace(/^-+|-+$/g, "");
    const renderSkuOptionSelector = () => {
        if (!skuOptionSelector) return;
        if (!options.length) {
            skuOptionSelector.innerHTML = '<span>无变体属性，将只使用基础 SKU</span>';
            return;
        }
        skuOptionSelector.innerHTML = options.map((option, index) => `
            <label class="form-check form-check-inline">
                <input class="form-check-input" type="checkbox" value="${index}" data-sku-option-index checked>
                <span>-第${index + 1}个变体属性（${escapeHtml(option.name)}）</span>
            </label>
        `).join("");
    };
    const editorHistory = [];
    let optionEditSnapshot = null;
    const captureEditorState = () => ({
        options: deepClone(readOptions()),
        variants: deepClone(Array.from(readVariantTable().values())),
    });
    const rememberEditorState = (state = null) => {
        editorHistory.push(state || captureEditorState());
        if (editorHistory.length > 50) editorHistory.shift();
    };
    const combinations = () => options.length ? options.reduce(
        (rows, option) => rows.flatMap((row) => option.values.map((value) => ({ ...row, [option.name]: value }))), [{}]
    ) : [{}];
    const renderVariants = () => {
        const previous = variantBody.children.length ? readVariantTable() : new Map(variants.map((item) => [signature(item.options), item]));
        const previousValues = Array.from(previous.values());
        const fallback = previousValues[0] || variants[0] || {};
        variants = combinations().map((values, index) => {
            const exact = previous.get(signature(values));
            if (exact) return { ...exact, options: values };
            const inherited = previousValues.find((item) => Object.entries(item.options || {}).every(
                ([name, value]) => values[name] === value
            )) || fallback;
            return {
                ...inherited,
                id: 0,
                options: values,
                image_url: inherited.image_url || "",
                sku: inherited.sku ? `${inherited.sku}-${index + 1}` : `SKU-${String(index + 1).padStart(3, "0")}`,
                price: inherited.price || "",
                compare_at_price: inherited.compare_at_price || "",
                inventory_quantity: inherited.inventory_quantity ?? 0,
                weight_kg: inherited.weight_kg || "",
                package_length_cm: inherited.package_length_cm || "",
                package_width_cm: inherited.package_width_cm || "",
                package_height_cm: inherited.package_height_cm || "",
            };
        });
        variantHead.innerHTML = `<tr><th>\u53d8\u4f53\u56fe\u7247</th>${options.map((option) => `<th>${escapeHtml(option.name)}</th>`).join("")}<th>SKU</th><th>\u552e\u4ef7</th><th>\u539f\u4ef7</th><th>\u5e93\u5b58</th><th>\u91cd\u91cf(kg)</th><th>\u5305\u88c5\u957f\u5bbd\u9ad8(cm)</th></tr>`;
        variantBody.innerHTML = variants.map((variant, index) => {
            const image = variant.image_url ? `<img src="${escapeHtml(variant.image_url)}" alt="">` : `<span class="variant-image-empty"><i class="bi bi-image"></i></span>`;
            return `<tr data-variant-row="${index}" data-image-url="${escapeHtml(variant.image_url || "")}">
                <td class="variant-image-cell">${image}<input class="visually-hidden" id="variant-image-${index}" type="file" name="variant_image-${index}" accept="image/jpeg,image/png,image/webp" data-variant-image-input><label class="btn btn-sm btn-light" for="variant-image-${index}">\u4e0a\u4f20</label></td>
                ${options.map((option) => `<td>${escapeHtml(variant.options?.[option.name] || "")}</td>`).join("")}
                <td><input type="hidden" name="variant_id-${index}" value="${Number(variant.id || 0)}"><input type="hidden" name="variant_options-${index}" value="${escapeHtml(JSON.stringify(variant.options || {}))}"><input class="form-control form-control-sm" required name="variant_sku-${index}" value="${escapeHtml(variant.sku || "")}"></td>
                <td><input class="form-control form-control-sm" type="number" min="0" step="0.01" required name="variant_price-${index}" value="${escapeHtml(variant.price ?? "")}"></td>
                <td><input class="form-control form-control-sm" type="number" min="0" step="0.01" name="variant_compare_at-${index}" value="${escapeHtml(variant.compare_at_price ?? "")}"></td>
                <td><input class="form-control form-control-sm" type="number" min="0" step="1" required name="variant_inventory-${index}" value="${escapeHtml(variant.inventory_quantity ?? 0)}"></td>
                <td><input class="form-control form-control-sm" type="number" min="0" step="0.001" name="variant_weight-${index}" value="${escapeHtml(variant.weight_kg ?? "")}"></td>
                <td><div class="dimension-fields"><input class="form-control form-control-sm" type="number" min="0" step="0.01" name="variant_length-${index}" value="${escapeHtml(variant.package_length_cm ?? "")}" placeholder="L"><input class="form-control form-control-sm" type="number" min="0" step="0.01" name="variant_width-${index}" value="${escapeHtml(variant.package_width_cm ?? "")}" placeholder="W"><input class="form-control form-control-sm" type="number" min="0" step="0.01" name="variant_height-${index}" value="${escapeHtml(variant.package_height_cm ?? "")}" placeholder="H"></div></td>
            </tr>`;
        }).join("");
        variantCount.value = variants.length;
        variantCountLabel.textContent = `\u5171 ${variants.length} \u4e2a\u53d8\u4f53`;
        optionsJson.value = JSON.stringify(options);
    };
    const renderOptions = () => {
        optionEditor.innerHTML = options.map((option, index) => `<div class="option-editor-row" data-option-row="${index}"><input class="form-control" data-option-name value="${escapeHtml(option.name || "")}" placeholder="\u5c5e\u6027\u540d\u79f0"><input class="form-control" data-option-values value="${escapeHtml((option.values || []).join(", "))}" placeholder="\u5c5e\u6027\u503c\uff0c\u7528\u9017\u53f7\u5206\u9694"><button class="btn btn-outline-danger" type="button" data-remove-option="${index}"><i class="bi bi-trash"></i></button></div>`).join("");
        optionEditor.querySelectorAll("input").forEach((input) => {
            input.addEventListener("focus", () => { optionEditSnapshot = captureEditorState(); }, { once: true });
            input.addEventListener("change", () => {
                rememberEditorState(optionEditSnapshot);
                optionEditSnapshot = null;
                variants = Array.from(readVariantTable().values()); options = readOptions(); variantBody.innerHTML = ""; renderOptions(); renderVariants();
            });
        });
        optionEditor.querySelectorAll("[data-remove-option]").forEach((button) => button.addEventListener("click", () => {
            rememberEditorState();
            variants = Array.from(readVariantTable().values()); options = readOptions(); options.splice(Number(button.dataset.removeOption), 1); variantBody.innerHTML = ""; renderOptions(); renderVariants();
        }));
        addOption.disabled = options.length >= 3;
        renderSkuOptionSelector();
    };
    addOption?.addEventListener("click", () => {
        if (options.length >= 3) return;
        rememberEditorState();
        variants = Array.from(readVariantTable().values());
        options.push({ name: `Option ${options.length + 1}`, values: ["Default"] });
        variantBody.innerHTML = "";
        renderOptions(); renderVariants();
    });
    generateSkuButton?.addEventListener("click", () => {
        const baseSku = normalizeSkuPart(baseSkuInput?.value);
        if (!baseSku) {
            window.alert("请先填写基础 SKU。");
            baseSkuInput?.focus();
            return;
        }
        const selectedIndexes = Array.from(
            skuOptionSelector?.querySelectorAll("[data-sku-option-index]:checked") || []
        ).map((input) => Number(input.value));
        if (variantBody.querySelectorAll("[data-variant-row]").length > 1 && !selectedIndexes.length) {
            window.alert("多个变体至少需要选择一个拼接属性，以避免生成重复 SKU。");
            return;
        }
        rememberEditorState();
        variantBody.querySelectorAll("[data-variant-row]").forEach((row) => {
            const index = row.dataset.variantRow;
            const values = JSON.parse(row.querySelector(`[name="variant_options-${index}"]`).value);
            const suffixes = selectedIndexes.map((optionIndex) => (
                normalizeSkuPart(values[options[optionIndex]?.name])
            )).filter(Boolean);
            row.querySelector(`[name="variant_sku-${index}"]`).value = [baseSku, ...suffixes].join("-").slice(0, 255);
        });
        variants = Array.from(readVariantTable().values());
    });
    variantBody.addEventListener("change", (event) => {
        const input = event.target.closest("[data-variant-image-input]");
        if (!input || !input.files?.[0]) return;
        const cell = input.closest(".variant-image-cell");
        let preview = cell.querySelector("img");
        if (!preview) {
            cell.querySelector(".variant-image-empty")?.remove();
            preview = document.createElement("img");
            preview.alt = "";
            cell.prepend(preview);
        }
        if (preview.dataset.previewUrl) URL.revokeObjectURL(preview.dataset.previewUrl);
        const previewUrl = URL.createObjectURL(input.files[0]);
        preview.src = previewUrl;
        preview.dataset.previewUrl = previewUrl;
    });
    editorForm.addEventListener("keydown", (event) => {
        if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z" || event.shiftKey) return;
        const inOptionEditor = event.target.closest("[data-option-editor]");
        const nativeUndoTarget = event.target.matches("input, textarea") || event.target.isContentEditable;
        if (!inOptionEditor && nativeUndoTarget) return;
        const state = editorHistory.pop();
        if (!state) return;
        event.preventDefault();
        options = deepClone(state.options);
        variants = deepClone(state.variants);
        optionEditSnapshot = null;
        variantBody.innerHTML = "";
        renderOptions();
        renderVariants();
    });
    renderOptions(); renderVariants();

    editorForm.querySelectorAll("[data-rich-command]").forEach((button) => button.addEventListener("click", () => {
        document.execCommand(button.dataset.richCommand, false, null);
        editorForm.querySelector("[data-rich-editor]")?.focus();
    }));
    editorForm.addEventListener("submit", () => {
        options = readOptions(); optionsJson.value = JSON.stringify(options);
        editorForm.querySelector("[data-rich-html]").value = editorForm.querySelector("[data-rich-editor]").innerHTML;
    });
});
