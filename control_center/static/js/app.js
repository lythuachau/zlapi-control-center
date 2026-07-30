(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function toast(message, type = "success") {
    const stack = $("#toast-stack");
    if (!stack) return;
    const item = document.createElement("div");
    item.className = "toast";
    const mark = document.createElement("span");
    mark.className = `mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${type === "error" ? "bg-rose-500" : "bg-emerald-500"}`;
    const text = document.createElement("p");
    text.className = "text-sm font-medium";
    text.textContent = message;
    item.append(mark, text);
    stack.append(item);
    setTimeout(() => item.remove(), 5000);
  }

  function formatApiError(payload, status) {
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return String(item);
        const location = Array.isArray(item.loc)
          ? item.loc.filter((part) => !["body", "query", "path"].includes(part)).join(".")
          : "";
        const message = item.msg || item.message || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      }).filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
    if (detail && typeof detail === "object") {
      return detail.message || JSON.stringify(detail);
    }
    return `HTTP ${status}`;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (!(options.body instanceof FormData) && options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
      headers.set("X-CSRF-Token", csrf);
    }
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, response.status));
    return payload;
  }

  window.ZLAPIControl = Object.freeze({ api, toast });

  function parseCookies(rawValue) {
    const raw = String(rawValue || "").trim();
    if (!raw) throw new Error("Cookie không được để trống.");
    if (raw.startsWith("{")) {
      const parsed = JSON.parse(raw);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("Cookie JSON phải là một object.");
      }
      if (!Object.keys(parsed).length || Object.values(parsed).some((value) => typeof value !== "string")) {
        throw new Error("Cookie JSON phải có ít nhất một cặp tên và giá trị dạng chuỗi.");
      }
      return parsed;
    }
    if (!raw.includes("=")) {
      return { zpw_sek: raw };
    }
    const parsed = {};
    for (const segment of raw.split(";")) {
      const pair = segment.trim();
      if (!pair) continue;
      const separator = pair.indexOf("=");
      if (separator <= 0) throw new Error(`Cookie không hợp lệ: ${pair}`);
      parsed[pair.slice(0, separator).trim()] = pair.slice(separator + 1).trim();
    }
    if (!Object.keys(parsed).length) {
      throw new Error("Cookie phải là JSON object hoặc chuỗi name=value.");
    }
    return parsed;
  }

  function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add("open");
  }
  function closeModal(target) {
    target.closest(".modal-backdrop")?.classList.remove("open");
  }

  const savedTheme = localStorage.getItem("zlapi-theme") || "dark";
  document.documentElement.dataset.theme = savedTheme;
  $("#theme-toggle")?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("zlapi-theme", next);
  });
  $("#menu-toggle")?.addEventListener("click", () => $("#sidebar")?.classList.toggle("open"));
  $$("[data-open-modal]").forEach((button) => button.addEventListener("click", () => openModal(button.dataset.openModal)));
  $$("[data-close-modal]").forEach((button) => button.addEventListener("click", () => closeModal(button)));
  $$(".modal-backdrop").forEach((backdrop) => backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) backdrop.classList.remove("open");
  }));

  $("#password-change-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") || "");
    const newPassword = String(form.get("new_password") || "");
    const confirmPassword = String(form.get("confirm_password") || "");
    const submit = $("#password-change-submit");

    if (newPassword.length < 12) {
      toast("Mật khẩu mới phải có tối thiểu 12 ký tự.", "error");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast("Hai lần nhập mật khẩu mới chưa khớp.", "error");
      return;
    }

    if (submit) submit.disabled = true;
    try {
      await api("/api/v1/me/password", {
        method: "PUT",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword
        })
      });
      event.currentTarget.reset();
      toast("Đã đổi mật khẩu. Đang chuyển tới trang đăng nhập…");
      setTimeout(() => location.assign("/login?changed=1"), 800);
    } catch (error) {
      toast(error.message, "error");
      if (submit) submit.disabled = false;
    }
  });

  $("#account-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let cookies;
    try {
      cookies = parseCookies(form.get("cookies"));
    } catch (error) {
      toast(error.message || "Cookie không hợp lệ.", "error");
      return;
    }
    try {
      const account = await api("/api/v1/accounts", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("name"),
          imei: form.get("imei"),
          cookies,
          user_agent: form.get("user_agent") || null,
          listener_enabled: form.get("listener_enabled") === "on"
        })
      });
      toast(`Đã thêm ${account.name}.`);
      setTimeout(() => location.reload(), 600);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $$("[data-update-account]").forEach((button) => button.addEventListener("click", () => {
    const form = $("#session-form");
    form?.reset();
    $("#session-account-id").value = button.dataset.updateAccount;
    $("#session-account-name").textContent = button.dataset.updateAccountName;
    openModal("session-modal");
  }));

  $("#session-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    let cookies;
    try {
      cookies = parseCookies(form.get("cookies"));
    } catch (error) {
      toast(error.message || "Cookie không hợp lệ.", "error");
      return;
    }
    const body = { cookies };
    const imei = String(form.get("imei") || "").trim();
    if (imei) body.imei = imei;
    try {
      const account = await api(`/api/v1/accounts/${form.get("account_id")}`, {
        method: "PATCH",
        body: JSON.stringify(body)
      });
      toast(`Đã cập nhật phiên ${account.name}. Listener vẫn đang tắt.`);
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $$("[data-test-account]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = await api(`/api/v1/accounts/${button.dataset.testAccount}/test`, { method: "POST" });
      toast(`Đã xếp job kiểm tra ${result.job_id.slice(0, 8)}.`);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }));

  $$("[data-toggle-listener]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const enabled = button.dataset.listenerState;
      await api(`/api/v1/accounts/${button.dataset.toggleListener}/listener/${enabled}`, { method: "POST" });
      toast(enabled === "true" ? "Listener sẽ được khởi động." : "Listener sẽ được dừng.");
      setTimeout(() => location.reload(), 500);
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  }));

  $$("[data-delete-account]").forEach((button) => button.addEventListener("click", () => {
    const form = $("#delete-account-form");
    form?.reset();
    $("#delete-account-id").value = button.dataset.deleteAccount;
    $("#delete-account-name").textContent = button.dataset.deleteAccountName;
    $("#delete-account-confirmation").placeholder = button.dataset.deleteAccountName;
    openModal("delete-account-modal");
  }));

  $("#delete-account-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const accountId = String(form.get("account_id") || "");
    const accountName = $("#delete-account-name").textContent;
    const typedName = String(form.get("confirmation") || "");
    if (typedName !== accountName) {
      toast("Tên tài khoản xác nhận không khớp.", "error");
      return;
    }
    if (form.get("final_confirmation") !== "on") {
      toast("Hãy đánh dấu xác nhận cuối cùng.", "error");
      return;
    }
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const result = await api(`/api/v1/accounts/${accountId}`, {
        method: "DELETE",
        body: JSON.stringify({ confirmation: typedName })
      });
      toast(`Đã xóa ${result.name}; lịch sử vẫn được giữ.`);
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
    }
  });

  let catalog = [];
  let selectedAction = null;
  const actionFields = $("#action-fields");

  function makeField(field) {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    if (["object", "array"].includes(field.type)) wrapper.classList.add("sm:col-span-2");
    const label = document.createElement("label");
    label.textContent = `${field.name}${field.required ? " *" : ""}`;
    label.htmlFor = `field-${field.name}`;
    let input;
    if (field.type === "thread_type") {
      input = document.createElement("select");
      [["user", "User"], ["group", "Group"]].forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      });
    } else if (field.type === "boolean") {
      input = document.createElement("select");
      [["true", "True"], ["false", "False"]].forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      });
    } else if (["object", "array"].includes(field.type)) {
      input = document.createElement("textarea");
      input.rows = 4;
      input.classList.add("font-mono", "text-xs");
      input.placeholder = field.type === "array" ? "[]" : "{}";
    } else {
      input = document.createElement("input");
      input.type = field.type === "number" ? "number" : field.type === "upload" ? "file" : "text";
    }
    input.id = `field-${field.name}`;
    input.name = field.name;
    input.dataset.fieldType = field.type;
    input.classList.add("input");
    input.required = field.required;
    if (field.default !== null && field.default !== undefined && field.type !== "upload") {
      input.value = ["object", "array"].includes(field.type)
        ? JSON.stringify(field.default)
        : String(field.default);
    }
    wrapper.append(label, input);
    return wrapper;
  }

  function selectAction(name) {
    selectedAction = catalog.find((item) => item.name === name);
    if (!selectedAction) return;
    $("#action-title").textContent = selectedAction.name;
    $("#action-description").textContent = selectedAction.description || `${selectedAction.category} · chạy bất đồng bộ qua worker`;
    const status = $("#action-status");
    status.className = `badge-${selectedAction.status}`;
    status.textContent = selectedAction.status;
    actionFields.replaceChildren(...selectedAction.fields.map(makeField));
    $("#danger-warning").classList.toggle("hidden", !selectedAction.dangerous);
    $("#run-action").disabled = selectedAction.status === "unsupported";
    $$(".action-item").forEach((item) => item.classList.toggle("surface-muted", item.dataset.action === name));
  }

  async function uploadFile(file) {
    const body = new FormData();
    body.append("file", file);
    const result = await api("/api/v1/uploads", { method: "POST", body });
    return result.token;
  }

  if ($("#action-list")) {
    api("/api/v1/catalog").then((items) => {
      catalog = items;
      const first = catalog.find((item) => item.status !== "unsupported");
      if (first) selectAction(first.name);
    }).catch((error) => toast(error.message, "error"));
    $$(".action-item").forEach((button) => button.addEventListener("click", () => selectAction(button.dataset.action)));
    $("#action-search")?.addEventListener("input", (event) => {
      const term = event.target.value.toLowerCase();
      $$(".action-item").forEach((item) => item.classList.toggle("hidden", !item.dataset.action.toLowerCase().includes(term)));
    });
    $("#action-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!selectedAction) return;
      const accountId = $("#action-account").value;
      if (!accountId) return toast("Hãy chọn tài khoản.", "error");
      let confirmation = null;
      if (selectedAction.dangerous) {
        if (!window.confirm(`Bạn sắp chạy ${selectedAction.name}. Tiếp tục bước xác nhận thứ hai?`)) return;
        confirmation = window.prompt(`Nhập chính xác "${selectedAction.name}" để xác nhận:`);
        if (confirmation !== selectedAction.name) return toast("Xác nhận không khớp.", "error");
      }
      const payload = {};
      const submit = $("#run-action");
      submit.disabled = true;
      try {
        for (const input of $$("[data-field-type]", actionFields)) {
          if (input.type === "file") {
            if (input.files.length) payload[input.name] = await uploadFile(input.files[0]);
            continue;
          }
          if (input.value === "") continue;
          const type = input.dataset.fieldType;
          if (["object", "array"].includes(type)) payload[input.name] = JSON.parse(input.value);
          else if (type === "number") payload[input.name] = Number(input.value);
          else if (type === "boolean") payload[input.name] = input.value === "true";
          else payload[input.name] = input.value;
        }
        const result = await api(`/api/v1/actions/${selectedAction.name}`, {
          method: "POST",
          body: JSON.stringify({ account_id: accountId, payload, confirmation })
        });
        $("#action-response-wrap").classList.remove("hidden");
        $("#action-response").textContent = JSON.stringify(result, null, 2);
        toast(`Job ${result.job_id.slice(0, 8)} đã được xếp hàng.`);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        submit.disabled = selectedAction.status === "unsupported";
      }
    });
  }

  async function mutateJob(id, action) {
    try {
      await api(`/api/v1/jobs/${id}/${action}`, { method: "POST" });
      toast(action === "retry" ? "Đã xếp lại job." : "Đã hủy job.");
      setTimeout(() => location.reload(), 500);
    } catch (error) {
      toast(error.message, "error");
    }
  }
  $$("[data-retry-job]").forEach((button) => button.addEventListener("click", () => mutateJob(button.dataset.retryJob, "retry")));
  $$("[data-cancel-job]").forEach((button) => button.addEventListener("click", () => mutateJob(button.dataset.cancelJob, "cancel")));
  $("#refresh-jobs")?.addEventListener("click", () => location.reload());
  if (document.body.dataset.page === "jobs") setInterval(() => {
    if (!document.hidden && !document.querySelector(".modal-backdrop.open")) location.reload();
  }, 5000);

  $("#user-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api("/api/v1/users", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(form.entries()))
      });
      toast("Đã tạo người dùng.");
      setTimeout(() => location.reload(), 500);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#key-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const expires = form.get("expires_at");
      const result = await api("/api/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("name"),
          scopes: form.get("scopes").split(",").map((value) => value.trim()).filter(Boolean),
          expires_at: expires ? new Date(expires).toISOString() : null
        })
      });
      window.prompt("Sao chép API key ngay — hệ thống sẽ không hiển thị lại:", result.api_key);
      location.reload();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $$("[data-revoke-key]").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("Thu hồi API key này? Hành động không thể hoàn tác.")) return;
    try {
      await api(`/api/v1/api-keys/${button.dataset.revokeKey}/revoke`, { method: "POST" });
      toast("Đã thu hồi API key.");
      setTimeout(() => location.reload(), 500);
    } catch (error) {
      toast(error.message, "error");
    }
  }));

  $("#check-health")?.addEventListener("click", async () => {
    try {
      const result = await api("/api/v1/health");
      $("#health-result").classList.remove("hidden");
      $("#health-result pre").textContent = JSON.stringify(result, null, 2);
      toast("Health check hoàn tất.");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  if (csrf && window.EventSource && document.body.dataset.page !== "chat") {
    const stream = new EventSource("/api/v1/events/stream");
    stream.addEventListener("job.succeeded", (event) => {
      const payload = JSON.parse(event.data);
      toast(`Job ${payload.payload?.job_id?.slice(0, 8) || ""} hoàn tất.`);
    });
    stream.addEventListener("job.failed", (event) => {
      const payload = JSON.parse(event.data);
      toast(`Job ${payload.payload?.job_id?.slice(0, 8) || ""} thất bại.`, "error");
    });
  }
})();
